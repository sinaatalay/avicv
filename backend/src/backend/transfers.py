"""Bounded direct file transfer for remote agents, separate from model context."""

import hashlib
import secrets
import time
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .auth import Principal, PrincipalDep, authorize_credential_id, authorize_resume
from .config import Settings, get_settings
from .db import connect_app
from .quota import check_quota
from .resumes import service


def private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(tags=["agent files"], dependencies=[Depends(private_response)])
SettingsDep = Annotated[Settings, Depends(get_settings)]
MAX_BYTES = 10 * 1024 * 1024


def initialize(settings: Settings) -> None:
    conn = connect_app(settings.app_db)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS upload_tickets (
            id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, credential_id TEXT NOT NULL,
            secret_hash TEXT NOT NULL, filename TEXT NOT NULL, size_bytes INTEGER NOT NULL,
            sha256 TEXT, expires_at REAL NOT NULL, resume_id TEXT NOT NULL,
            accepted INTEGER NOT NULL DEFAULT 0
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS download_grants (
            secret_hash TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
            credential_id TEXT NOT NULL, resume_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
            expires_at REAL NOT NULL
        )""")
        conn.commit()
    finally:
        conn.close()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class UploadMetadata(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0, le=MAX_BYTES)
    sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")


def begin_upload(
    principal: Principal, metadata: UploadMetadata, settings: Settings
) -> dict:
    initialize(settings)
    upload_id, token, resume_id = uuid4().hex, secrets.token_urlsafe(32), uuid4().hex
    expires = time.time() + 600
    conn = connect_app(settings.app_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        check_quota(conn, settings, principal, "upload_ticket")
        conn.execute(
            "INSERT INTO upload_tickets(id,workspace_id,credential_id,secret_hash,filename,"
            "size_bytes,sha256,expires_at,resume_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                upload_id,
                principal.workspace_id,
                principal.credential_id,
                digest(token),
                metadata.filename,
                metadata.size_bytes,
                metadata.sha256.lower() if metadata.sha256 else None,
                expires,
                resume_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "upload_id": upload_id,
        "upload_url": f"{settings.app_origin.rstrip('/')}/uploads/{upload_id}",
        "method": "PUT",
        "headers": {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/pdf",
        },
        "expires_at": expires,
        "max_bytes": MAX_BYTES,
        "instructions": "Upload actual PDF bytes from the local file to this URL. Do not send a local path or base64 to MCP. Keep the temporary upload credential out of logs and chat.",
    }


def read_upload(principal: Principal, upload_id: str, settings: Settings) -> dict:
    initialize(settings)
    conn = connect_app(settings.app_db)
    try:
        row = conn.execute(
            "SELECT t.*, r.id AS saved FROM upload_tickets t LEFT JOIN resumes r "
            "ON r.id=t.resume_id AND r.workspace_id=t.workspace_id "
            "WHERE t.id=? AND t.workspace_id=?",
            (upload_id, principal.workspace_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Upload not found.")
        return {
            "upload_id": upload_id,
            "status": "accepted"
            if row["saved"]
            else "expired"
            if row["expires_at"] < time.time()
            else "waiting_for_file",
            "resume_id": row["saved"],
            "page_url": f"{settings.app_origin.rstrip('/')}/resumes/{row['saved']}"
            if row["saved"]
            else None,
        }
    finally:
        conn.close()


def validate_ticket(upload_id: str, token: str, settings: Settings) -> dict:
    initialize(settings)
    conn = connect_app(settings.app_db)
    try:
        row = conn.execute(
            "SELECT * FROM upload_tickets WHERE id=?", (upload_id,)
        ).fetchone()
        if not row or not secrets.compare_digest(row["secret_hash"], digest(token)):
            raise HTTPException(401, "Invalid upload credential.")
        if row["expires_at"] < time.time():
            raise HTTPException(410, "Upload link expired. Request a new upload.")
        return dict(row)
    finally:
        conn.close()


@router.put("/uploads/{upload_id}", status_code=202)
async def accept_upload(
    upload_id: str, request: Request, settings: SettingsDep
) -> dict:
    authorization = request.headers.get("authorization", "")
    token = authorization[7:] if authorization.lower().startswith("bearer ") else ""
    ticket = await run_in_threadpool(validate_ticket, upload_id, token, settings)
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/pdf":
        raise HTTPException(415, "Send PDF bytes with Content-Type: application/pdf.")
    chunks = bytearray()
    async for chunk in request.stream():
        if len(chunks) + len(chunk) > min(ticket["size_bytes"], MAX_BYTES):
            raise HTTPException(413, "PDF exceeds the upload's declared size.")
        chunks.extend(chunk)
    if len(chunks) != ticket["size_bytes"]:
        raise HTTPException(422, "PDF size does not match the upload metadata.")
    if ticket["sha256"] and hashlib.sha256(chunks).hexdigest() != ticket["sha256"]:
        raise HTTPException(422, "PDF checksum does not match.")
    # The reserved resume ID makes acceptance recoverable even after a response is lost.
    principal = await run_in_threadpool(
        authorize_credential_id,
        ticket["credential_id"],
        ticket["workspace_id"],
        settings,
    )
    created = await run_in_threadpool(
        service.create_resume,
        principal,
        bytes(chunks),
        ticket["filename"],
        settings,
        resume_id=ticket["resume_id"],
    )
    conn = connect_app(settings.app_db)
    try:
        conn.execute("UPDATE upload_tickets SET accepted=1 WHERE id=?", (upload_id,))
        conn.commit()
    finally:
        conn.close()
    return {
        "id": created.id,
        "page_url": f"{settings.app_origin.rstrip('/')}/resumes/{created.id}",
    }


def download_link(
    principal: Principal, resume_id: str, artifact_id: str, settings: Settings
) -> str:
    initialize(settings)
    service.get_tailored(principal, resume_id, artifact_id, settings)
    token = secrets.token_urlsafe(32)
    conn = connect_app(settings.app_db)
    try:
        conn.execute("DELETE FROM download_grants WHERE expires_at < ?", (time.time(),))
        conn.execute(
            "INSERT INTO download_grants VALUES(?,?,?,?,?,?)",
            (
                digest(token),
                principal.workspace_id,
                principal.credential_id,
                resume_id,
                artifact_id,
                time.time() + 600,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return f"{settings.app_origin.rstrip('/')}/downloads/{token}"


@router.get("/downloads/{token}")
def download(token: str, settings: SettingsDep) -> Response:
    initialize(settings)
    conn = connect_app(settings.app_db)
    try:
        grant = conn.execute(
            "SELECT * FROM download_grants WHERE secret_hash=? AND expires_at>?",
            (digest(token), time.time()),
        ).fetchone()
        if not grant:
            raise HTTPException(
                404,
                "Download link expired or unavailable. Request a fresh link in AviCV.",
            )
        principal = authorize_credential_id(
            grant["credential_id"], grant["workspace_id"], settings
        )
        authorize_resume(conn, grant["resume_id"], principal)
        data, filename = service.read_tailored_pdf(
            principal, grant["resume_id"], grant["artifact_id"], settings
        )
    finally:
        conn.close()
    return Response(
        data,
        media_type="application/pdf",
        headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/uploads/{upload_id}")
def upload_status(
    upload_id: str, principal: PrincipalDep, settings: SettingsDep
) -> dict:
    return read_upload(principal, upload_id, settings)
