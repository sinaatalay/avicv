"""Anonymous browser workspaces and revocable agent credentials.

A résumé ID identifies a resource; it never authenticates its caller. Only hashes
of randomly generated credentials are persisted, and legacy ownerless résumés
remain private until an administrator explicitly migrates them.
"""

import hashlib
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .config import Settings, get_settings
from .db import connect_app
from .quota import check_quota

COOKIE_NAME = "avicv_session"
SESSION_SECONDS = 365 * 24 * 60 * 60
PRIVATE_HEADERS = {"Cache-Control": "private, no-store"}
SettingsDep = Annotated[Settings, Depends(get_settings)]
router = APIRouter(prefix="/auth", tags=["workspace"])


@dataclass(frozen=True)
class Principal:
    workspace_id: str
    credential_id: str
    kind: Literal["session", "token"]


class Workspace(BaseModel):
    workspace_id: str


class TokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class TokenMetadata(BaseModel):
    id: str
    name: str
    created_at: str
    last_used_at: str | None
    revoked_at: str | None


class CreatedToken(TokenMetadata):
    token: str


class TokenList(BaseModel):
    items: list[TokenMetadata]


def timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def credential_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def unauthorized() -> HTTPException:
    return HTTPException(
        401,
        "Connect to your AviCV workspace first.",
        headers={"WWW-Authenticate": "Bearer", **PRIVATE_HEADERS},
    )


def resolve_credential(
    raw: str, settings: Settings, kind: Literal["session", "token"] | None = None
) -> Principal:
    """Resolve a cookie/PAT without accepting resource IDs or disclosing secrets."""
    if not raw or len(raw) > 256:
        raise unauthorized()
    conn = connect_app(settings.app_db)
    try:
        row = conn.execute(
            "SELECT id, workspace_id, kind, expires_at, revoked_at FROM credentials WHERE secret_hash = ?",
            (credential_hash(raw),),
        ).fetchone()
        if (
            row is None
            or row["revoked_at"] is not None
            or (kind is not None and row["kind"] != kind)
            or (row["expires_at"] is not None and row["expires_at"] <= time.time())
        ):
            raise unauthorized()
        conn.execute(
            "UPDATE credentials SET last_used_at = ? WHERE id = ?",
            (timestamp(), row["id"]),
        )
        conn.commit()
        return Principal(row["workspace_id"], row["id"], row["kind"])
    finally:
        conn.close()


def check_cookie_origin(request: Request, settings: Settings) -> None:
    """Block cross-origin cookie mutations while allowing nonbrowser agents.

    SvelteKit forwards the browser origin. APP_ORIGIN is the sole production
    origin; direct local development uses the request's own origin.
    """
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Use AviCV from its own website.")
    origin = request.headers.get("origin")
    expected = str(getattr(settings, "app_origin", "") or request.base_url).rstrip("/")
    if origin and origin.rstrip("/") != expected:
        raise HTTPException(403, "Use AviCV from its own website.")


def require_principal(request: Request, settings: SettingsDep) -> Principal:
    authorization = request.headers.get("authorization")
    if authorization is not None:
        scheme, _, raw = authorization.partition(" ")
        if scheme.lower() != "bearer":
            raise unauthorized()
        return resolve_credential(raw.strip(), settings, kind="token")
    check_cookie_origin(request, settings)
    return resolve_credential(
        request.cookies.get(COOKIE_NAME, ""), settings, kind="session"
    )


PrincipalDep = Annotated[Principal, Depends(require_principal)]


def authorize_credential_id(
    credential_id: str, workspace_id: str, settings: Settings
) -> Principal:
    """Revalidate the credential behind a bounded upload/download capability."""
    conn = connect_app(settings.app_db)
    try:
        row = conn.execute(
            "SELECT kind, expires_at, revoked_at FROM credentials WHERE id = ? AND workspace_id = ?",
            (credential_id, workspace_id),
        ).fetchone()
        if (
            row is None
            or row["revoked_at"] is not None
            or (row["expires_at"] is not None and row["expires_at"] <= time.time())
        ):
            raise unauthorized()
        return Principal(workspace_id, credential_id, row["kind"])
    finally:
        conn.close()


def authorize_resume(
    conn: sqlite3.Connection, resume_id: str, principal: Principal
) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM resumes WHERE id = ? AND workspace_id = ?",
            (resume_id, principal.workspace_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "Résumé not found.", headers=PRIVATE_HEADERS)


def require_browser(principal: Principal) -> None:
    # A copied agent token must not be able to mint an unbounded family of keys.
    if principal.kind != "session":
        raise HTTPException(403, "Manage agent connections from the AviCV website.")


@router.post("/session")
def create_session(
    request: Request, response: Response, settings: SettingsDep
) -> Workspace:
    check_cookie_origin(request, settings)
    response.headers.update(PRIVATE_HEADERS)
    raw = request.cookies.get(COOKIE_NAME)
    if raw:
        try:
            principal = resolve_credential(raw, settings, kind="session")
            return Workspace(workspace_id=principal.workspace_id)
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
    raw = "avs_" + secrets.token_urlsafe(32)
    workspace_id = uuid4().hex
    conn = connect_app(settings.app_db)
    try:
        with conn:
            conn.execute(
                "INSERT INTO workspaces(id, created_at) VALUES (?, ?)",
                (workspace_id, timestamp()),
            )
            conn.execute(
                "INSERT INTO credentials(id,workspace_id,kind,secret_hash,created_at,expires_at) VALUES (?,?,'session',?,?,?)",
                (
                    uuid4().hex,
                    workspace_id,
                    credential_hash(raw),
                    timestamp(),
                    time.time() + SESSION_SECONDS,
                ),
            )
    finally:
        conn.close()
    secure = (
        urlsplit(str(getattr(settings, "app_origin", "") or request.base_url)).scheme
        == "https"
    )
    response.set_cookie(
        COOKIE_NAME,
        raw,
        max_age=SESSION_SECONDS,
        secure=secure,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return Workspace(workspace_id=workspace_id)


@router.get("/session")
def get_session(principal: PrincipalDep, response: Response) -> Workspace:
    response.headers.update(PRIVATE_HEADERS)
    return Workspace(workspace_id=principal.workspace_id)


@router.post("/chat-admission")
def admit_chat(
    principal: PrincipalDep, settings: SettingsDep, response: Response
) -> dict[str, bool]:
    """Reserve paid chat usage before the private chat service calls its model."""
    conn = connect_app(settings.app_db)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            check_quota(conn, settings, principal, "chat")
    except HTTPException as exc:
        exc.headers = {**PRIVATE_HEADERS, **(exc.headers or {})}
        raise
    finally:
        conn.close()
    response.headers.update(PRIVATE_HEADERS)
    return {"accepted": True}


@router.post("/tokens", status_code=201)
def create_token(
    request: TokenRequest,
    principal: PrincipalDep,
    settings: SettingsDep,
    response: Response,
) -> CreatedToken:
    require_browser(principal)
    name = request.name.strip()
    if not name:
        raise HTTPException(422, "Give this connection a name.")
    raw = "avt_" + secrets.token_urlsafe(32)
    token_id, created_at = uuid4().hex, timestamp()
    conn = connect_app(settings.app_db)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            count = conn.execute(
                "SELECT COUNT(*) FROM credentials WHERE workspace_id = ? AND kind = 'token' AND revoked_at IS NULL",
                (principal.workspace_id,),
            ).fetchone()[0]
            if count >= 20:
                raise HTTPException(
                    429, "Remove an old connection before creating another."
                )
            conn.execute(
                "INSERT INTO credentials(id,workspace_id,kind,secret_hash,name,created_at) VALUES (?,?,'token',?,?,?)",
                (
                    token_id,
                    principal.workspace_id,
                    credential_hash(raw),
                    name,
                    created_at,
                ),
            )
    finally:
        conn.close()
    response.headers.update(PRIVATE_HEADERS)
    return CreatedToken(
        id=token_id,
        name=name,
        created_at=created_at,
        last_used_at=None,
        revoked_at=None,
        token=raw,
    )


@router.get("/tokens")
def list_tokens(
    principal: PrincipalDep, settings: SettingsDep, response: Response
) -> TokenList:
    require_browser(principal)
    conn = connect_app(settings.app_db)
    try:
        rows = conn.execute(
            "SELECT id,name,created_at,last_used_at,revoked_at FROM credentials WHERE workspace_id = ? AND kind = 'token' ORDER BY created_at DESC, id",
            (principal.workspace_id,),
        ).fetchall()
    finally:
        conn.close()
    response.headers.update(PRIVATE_HEADERS)
    return TokenList(items=[TokenMetadata(**dict(row)) for row in rows])


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(
    token_id: str, principal: PrincipalDep, settings: SettingsDep
) -> Response:
    require_browser(principal)
    conn = connect_app(settings.app_db)
    try:
        with conn:
            result = conn.execute(
                "UPDATE credentials SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ? AND workspace_id = ? AND kind = 'token'",
                (timestamp(), token_id, principal.workspace_id),
            )
            if not result.rowcount:
                raise HTTPException(404, "Connection not found.")
    finally:
        conn.close()
    return Response(status_code=204, headers=PRIVATE_HEADERS)
