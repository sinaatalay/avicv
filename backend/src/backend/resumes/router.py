"""Durable uploads, status, filtered result pages, retries, and deletion.

Résumé IDs are bearer capabilities, preserved for compatibility. This app is for a
trusted local deployment; adding public accounts requires an ownership boundary.
"""

import hashlib
import io
import json
import sqlite3
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from pypdf import PdfReader
from rendercv.schema.sample_generator import dictionary_to_yaml

from ..config import Settings, get_settings
from ..db import connect_app
from .catalog import catalog_states
from .parse import load_yaml
from .queue import enqueue

router = APIRouter(prefix="/resumes", tags=["resumes"])
SettingsDep = Annotated[Settings, Depends(get_settings)]
MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 10


class ResumeCreated(BaseModel):
    id: str


class Section(BaseModel):
    title: str
    entry_type: str
    entries: list[dict[str, Any] | str]


class Cv(BaseModel):
    name: str | None
    headline: str | None
    location: str | None
    sections: list[Section]


class Progress(BaseModel):
    done: int
    total: int
    attempted: int
    succeeded: int
    failed: int
    pending: int


class Preview(BaseModel):
    name: str | None
    sections: list[str]


LocationSource = Literal["resume", "default", "unknown"]


class Resume(BaseModel):
    id: str
    status: Literal[
        "queued", "parsing", "matching", "waiting_for_jobs", "done", "partial", "failed"
    ]
    error: str | None
    filename: str | None
    created_at: str
    preview: Preview | None
    cv: Cv | None
    rendercv_yaml: str | None
    progress: Progress
    search: dict[str, Any] | None
    cost_usd: float
    effective_location: str | None
    location_source: LocationSource
    results_are_previous: bool


class Match(BaseModel):
    job_id: int
    job_key: str
    title: str
    company: str | None
    locations: list[str]
    workplace_type: str | None
    url: str | None
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_interval: str | None
    posted_at: str | None
    score: float
    fit: float
    interview: float
    must_haves: float
    location: float | None
    seniority: Literal["below", "matches", "above"]
    provider: str | None
    board: str | None
    source_id: str | None
    catalog_status: Literal["open", "closed", "changed", "missing", "unverified"]
    questions_version: str
    model: str | None
    source_status: str
    source_verified_at: str | None
    source_stale: bool


class MatchResults(BaseModel):
    items: list[Match]
    total: int
    unfiltered_total: int
    nearby_total: int | None
    closed_total: int
    offset: int
    limit: int
    has_more: bool
    candidate_location_known: bool
    effective_location: str | None
    location_source: LocationSource
    results_are_previous: bool


class RematchRequest(BaseModel):
    location: str | None = Field(default=None, min_length=1, max_length=200)


@router.post("", status_code=202)
async def upload_resume(file: UploadFile, settings: SettingsDep) -> ResumeCreated:
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Upload a PDF smaller than 10 MB.")
    if b"%PDF-" not in data[:1024]:
        raise HTTPException(415, "Upload a PDF.")
    try:
        pages = len(PdfReader(io.BytesIO(data)).pages)
    except Exception as exc:
        raise HTTPException(422, "This PDF can't be opened.") from exc
    if pages > MAX_PAGES:
        raise HTTPException(422, f"Upload a résumé of at most {MAX_PAGES} pages.")
    if not pages:
        raise HTTPException(422, "Upload a PDF containing at least one page.")

    resume_id = uuid4().hex
    conn = connect_app(settings.app_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        ensure_queue_capacity(conn, settings)
        conn.execute(
            """
            INSERT INTO resumes (id, created_at, filename, pdf_sha256, status, upload_pdf)
            VALUES (?, ?, ?, ?, 'queued', ?)
        """,
            (
                resume_id,
                datetime.now(UTC).isoformat(timespec="seconds"),
                file.filename,
                hashlib.sha256(data).hexdigest(),
                data,
            ),
        )
        enqueue(conn, resume_id)
        conn.commit()
    finally:
        conn.close()
    return ResumeCreated(id=resume_id)


@router.get("/{resume_id}")
def get_resume(resume_id: str, settings: SettingsDep) -> Resume:
    row = fetch_resume(resume_id, settings)
    effective_location, location_source = location_context(row)
    return Resume(
        id=row["id"],
        status=row["status"],
        error=row["error"],
        filename=row["filename"],
        created_at=row["created_at"],
        preview=Preview.model_validate_json(row["parse_preview"])
        if row["parse_preview"]
        else None,
        cv=cv_for_display(row["rendercv_yaml"]) if row["rendercv_yaml"] else None,
        rendercv_yaml=row["rendercv_yaml"],
        progress=Progress(
            done=row["match_done"],
            succeeded=row["match_done"],
            total=row["match_total"],
            attempted=row["match_attempted"],
            failed=row["match_failed"],
            pending=max(
                0, row["match_total"] - row["match_done"] - row["match_failed"]
            ),
        ),
        search=json.loads(row["search_stats"]) if row["search_stats"] else None,
        cost_usd=(row["parse_cost_usd"] or 0) + row["match_cost_usd"],
        effective_location=effective_location,
        location_source=location_source,
        results_are_previous=row["current_generation"] != row["work_generation"]
        and bool(row["has_current_results"])
        and bool(row["has_requested_work"]),
    )


@router.get("/{resume_id}/rendercv.yaml", response_class=PlainTextResponse)
def get_rendercv_yaml(resume_id: str, settings: SettingsDep) -> PlainTextResponse:
    row = fetch_resume(resume_id, settings)
    if not row["rendercv_yaml"]:
        raise HTTPException(409, "This résumé hasn't been parsed yet.")
    return PlainTextResponse(
        row["rendercv_yaml"],
        media_type="application/yaml",
        headers={"Content-Disposition": 'attachment; filename="cv.yaml"'},
    )


@router.get("/{resume_id}/match-results")
def get_match_results(
    resume_id: str,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    nearby: bool = False,
    include_closed: bool = False,
) -> MatchResults:
    conn = connect_app(settings.app_db)
    try:
        conn.execute("BEGIN")
        resume = fetch_resume(resume_id, settings, connection=conn)
        effective_location, location_source = location_context(resume)
        known = effective_location is not None
        # Resolve every grouped member's stable identity before pagination. The
        # temporary table supports SQL filters without replacing snapshot fields.
        states = catalog_states(conn, resume_id, resume["current_generation"], settings)
        conn.execute("""
            CREATE TEMP TABLE match_catalog (
                job_key TEXT PRIMARY KEY, catalog_status TEXT NOT NULL,
                source_status TEXT NOT NULL, source_verified_at TEXT, source_stale INTEGER NOT NULL
            )
        """)
        conn.executemany(
            "INSERT INTO match_catalog VALUES (?, ?, ?, ?, ?)",
            [
                (
                    key,
                    state["catalog_status"],
                    state["source_status"],
                    state["source_verified_at"],
                    state["source_stale"],
                )
                for key, state in states.items()
            ],
        )
        join = "JOIN match_catalog c ON c.job_key = m.job_key"
        closed = "c.catalog_status = 'closed'"
        status = "c.catalog_status"
        base = f"FROM matches m {join} WHERE m.resume_id = ? AND m.generation = ?"
        params: list[Any] = [resume_id, resume["current_generation"]]
        # No current-questions filter: historical results remain visible until a
        # new generation has its durable plan, even after a question version bump.
        closed_total = conn.execute(
            f"SELECT COUNT(*) {base} AND ({closed})", params
        ).fetchone()[0]
        visible = base + (f" AND NOT ({closed})" if not include_closed else "")
        unfiltered_total = conn.execute(
            f"SELECT COUNT(*) {visible}", params
        ).fetchone()[0]
        nearby_total = (
            conn.execute(
                f"SELECT COUNT(*) {visible} AND m.location >= 0.5", params
            ).fetchone()[0]
            if known
            else None
        )
        filtered = visible + (" AND m.location >= 0.5" if nearby and known else "")
        total = (
            nearby_total if nearby and nearby_total is not None else unfiltered_total
        )
        rows = conn.execute(
            f"""
            SELECT m.*, {status} AS catalog_status, c.source_status, c.source_verified_at, c.source_stale {filtered}
            ORDER BY m.score DESC, m.job_key, m.questions LIMIT ? OFFSET ?
        """,
            [*params, limit, offset],
        ).fetchall()
    finally:
        conn.close()
    return MatchResults(
        items=[match_for_display(row, known) for row in rows],
        total=total,
        unfiltered_total=unfiltered_total,
        nearby_total=nearby_total,
        closed_total=closed_total,
        offset=offset,
        limit=limit,
        has_more=offset + len(rows) < total,
        candidate_location_known=known,
        effective_location=effective_location,
        location_source=location_source,
        results_are_previous=resume["current_generation"] != resume["work_generation"]
        and bool(unfiltered_total)
        and bool(resume["has_requested_work"]),
    )


@router.get("/{resume_id}/matches")
def get_matches(
    resume_id: str,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    nearby: bool = False,
) -> list[Match]:
    return get_match_results(resume_id, settings, limit, offset, nearby).items


@router.post("/{resume_id}/retry", status_code=202)
def retry_resume(resume_id: str, settings: SettingsDep) -> ResumeCreated:
    row = fetch_resume(resume_id, settings)
    if not row["rendercv_yaml"] and not has_upload(resume_id, settings):
        raise HTTPException(
            409, "The original PDF is unavailable. Please upload it again."
        )
    conn = connect_app(settings.app_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        ensure_not_running(conn, resume_id)
        ensure_queue_capacity(conn, settings, resume_id)
        # Legacy partial results have no work plan: rebuild it safely, reusing
        # verified identical results instead of dropping the successful ones.
        conn.execute(
            """
            UPDATE resume_work SET state = 'pending', error = NULL WHERE resume_id = ?
                AND candidate_revision = ? AND generation = ? AND state = 'failed'
        """,
            (resume_id, row["candidate_revision"], row["work_generation"]),
        )
        conn.execute(
            "UPDATE resumes SET status = 'queued', error = NULL, match_failed = 0 WHERE id = ?",
            (resume_id,),
        )
        enqueue(conn, resume_id)
        conn.commit()
    finally:
        conn.close()
    return ResumeCreated(id=resume_id)


@router.post("/{resume_id}/rematch", status_code=202)
def rematch_resume(
    resume_id: str,
    settings: SettingsDep,
    request: Annotated[RematchRequest | None, Body()] = None,
) -> ResumeCreated:
    row = fetch_resume(resume_id, settings)
    if not row["rendercv_yaml"]:
        raise HTTPException(409, "Wait for résumé parsing to finish before rematching.")
    yaml = row["rendercv_yaml"]
    revision = row["candidate_revision"]
    if request and request.location is not None:
        location = request.location.strip()
        if not location:
            raise HTTPException(422, "Enter a location.")
        model = load_yaml(yaml)
        if model.cv.location != location:
            doc = {"cv": json.loads(model.cv.model_dump_json(exclude_none=True))}
            doc["cv"]["location"] = location
            yaml = dictionary_to_yaml(doc)
            load_yaml(yaml)
            revision += 1
    conn = connect_app(settings.app_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        ensure_not_running(conn, resume_id)
        ensure_queue_capacity(conn, settings, resume_id)
        old_location, old_source = location_context(row)
        conn.execute(
            "UPDATE resumes SET results_context = COALESCE(results_context, ?) WHERE id = ?",
            (
                json.dumps(
                    {"effective_location": old_location, "location_source": old_source}
                ),
                resume_id,
            ),
        )
        conn.execute(
            """
            UPDATE resumes SET status = 'queued', error = NULL, work_generation = work_generation + 1,
                candidate_revision = ?, rendercv_yaml = ? WHERE id = ?
        """,
            (revision, yaml, resume_id),
        )
        enqueue(conn, resume_id)
        conn.commit()
    finally:
        conn.close()
    return ResumeCreated(id=resume_id)


@router.delete("/{resume_id}", status_code=204)
def delete_resume(resume_id: str, settings: SettingsDep) -> Response:
    conn = connect_app(settings.app_db)
    try:
        with conn:
            deleted = conn.execute("DELETE FROM resumes WHERE id = ?", (resume_id,))
            if not deleted.rowcount:
                raise HTTPException(404, "Résumé not found.")
    finally:
        conn.close()
    return Response(status_code=204)


def ensure_queue_capacity(
    conn: sqlite3.Connection, settings: Settings, resume_id: str = ""
) -> None:
    count = conn.execute(
        "SELECT COUNT(*) FROM resume_queue WHERE state != 'done' AND resume_id != ?",
        (resume_id,),
    ).fetchone()[0]
    if count >= settings.queue_max_pending:
        raise HTTPException(
            429,
            "The processing queue is full. Try again shortly.",
            headers={"Retry-After": "30"},
        )


def ensure_not_running(conn: sqlite3.Connection, resume_id: str) -> None:
    row = conn.execute(
        "SELECT state FROM resume_queue WHERE resume_id = ?", (resume_id,)
    ).fetchone()
    if row and row["state"] == "running":
        raise HTTPException(409, "This résumé is already being processed.")


def has_upload(resume_id: str, settings: Settings) -> bool:
    conn = connect_app(settings.app_db)
    try:
        return bool(
            conn.execute(
                "SELECT upload_pdf IS NOT NULL FROM resumes WHERE id = ?", (resume_id,)
            ).fetchone()[0]
        )
    finally:
        conn.close()


def fetch_resume(
    resume_id: str, settings: Settings, *, connection: sqlite3.Connection | None = None
) -> Any:
    conn = connection or connect_app(settings.app_db)
    try:
        # Avoid reading the potentially 10 MB PDF on every status poll.
        columns = [
            r["name"]
            for r in conn.execute("PRAGMA table_info(resumes)")
            if r["name"] != "upload_pdf"
        ]
        row = conn.execute(
            f"SELECT {', '.join(columns)}, EXISTS(SELECT 1 FROM matches WHERE resume_id = resumes.id AND generation = resumes.current_generation) AS has_current_results, EXISTS(SELECT 1 FROM resume_queue WHERE resume_id = resumes.id) AS has_requested_work FROM resumes WHERE id = ?",
            (resume_id,),
        ).fetchone()
    finally:
        if connection is None:
            conn.close()
    if row is None:
        raise HTTPException(404, "Résumé not found.")
    return row


def location_context(row: Any) -> tuple[str | None, LocationSource]:
    if row["results_context"]:
        context = json.loads(row["results_context"])
        return context.get("effective_location"), context.get(
            "location_source", "unknown"
        )
    location = (
        cv_for_display(row["rendercv_yaml"]).location if row["rendercv_yaml"] else None
    )
    # Historical locationless scores did not use the new US default. They remain
    # explicitly unknown until rescored, rather than being relabeled retrospectively.
    return location, "resume" if location else "unknown"


def match_for_display(row: Any, location_known: bool) -> Match:
    job = json.loads(row["job_snapshot"]) if row["job_snapshot"] else {}
    return Match(
        job_id=row["job_id"],
        job_key=row["job_key"],
        title=job.get("title", "Saved job (original details unavailable)"),
        company=job.get("company"),
        locations=json.loads(row["locations"]),
        workplace_type=job.get("workplace_type"),
        url=job.get("apply_url") or job.get("source_url"),
        salary_min=job.get("salary_min"),
        salary_max=job.get("salary_max"),
        salary_currency=job.get("salary_currency"),
        salary_interval=job.get("salary_interval"),
        posted_at=job.get("posted_at"),
        score=row["score"],
        fit=row["fit"],
        interview=row["interview"],
        must_haves=row["must_haves"],
        location=row["location"] if location_known else None,
        seniority=row["seniority"],
        provider=row["provider"],
        board=row["board"],
        source_id=row["source_id"],
        catalog_status=row["catalog_status"],
        questions_version=row["questions"],
        model=job.get("scoring_model"),
        source_status=row["source_status"],
        source_verified_at=row["source_verified_at"],
        source_stale=bool(row["source_stale"]),
    )


@lru_cache(maxsize=256)
def cv_for_display(rendercv_yaml: str) -> Cv:
    cv = load_yaml(rendercv_yaml).cv
    return Cv(
        name=cv.name,
        headline=cv.headline,
        location=cv.location,
        sections=[
            Section(
                title=section.title,
                entry_type=section.entry_type,
                entries=[
                    entry
                    if isinstance(entry, str)
                    else entry.model_dump(mode="json", exclude_none=True)
                    for entry in section.entries
                ],
            )
            for section in cv.rendercv_sections
        ],
    )
