"""Authorized application operations shared by REST and MCP transports."""

import hashlib
import io
import json
import sqlite3
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field
from pypdf import PdfReader
from rendercv.schema.sample_generator import dictionary_to_yaml

from ..auth import Principal, authorize_resume
from ..config import Settings
from ..db import connect_app
from ..quota import check_quota
from .artifact_store import ArtifactQueueFull, check_artifact_capacity
from .catalog import catalog_states
from .links import job_url
from .parse import load_yaml
from .queue import enqueue
from .savings import TimeSavings, time_savings

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 10
GOOD_MATCH_THRESHOLD = 0.80
MatchCategory = Literal["all", "good", "other"]
MATCH_ORDER = "m.score DESC, m.job_key, m.questions"


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
    good_total: int
    other_total: int
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


class GoodMatchLinks(BaseModel):
    links: list[str]


class RematchRequest(BaseModel):
    location: str | None = Field(default=None, min_length=1, max_length=200)


def validate_pdf(data: bytes) -> None:
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


def create_resume(
    principal: Principal,
    pdf_bytes: bytes,
    filename: str | None,
    settings: Settings,
    *,
    resume_id: str | None = None,
) -> ResumeCreated:
    validate_pdf(pdf_bytes)
    data = pdf_bytes
    resume_id = resume_id or uuid4().hex
    digest = hashlib.sha256(data).hexdigest()
    conn = connect_app(settings.app_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute(
            "SELECT 1 FROM deleted_resumes WHERE resume_id = ?", (resume_id,)
        ).fetchone():
            raise HTTPException(
                410, "This CV was deleted. Start a new upload to add it again."
            )
        existing = conn.execute(
            "SELECT workspace_id, pdf_sha256 FROM resumes WHERE id = ?", (resume_id,)
        ).fetchone()
        if existing is not None:
            if (
                existing["workspace_id"] != principal.workspace_id
                or existing["pdf_sha256"] != digest
            ):
                raise HTTPException(409, "This upload identifier is already in use.")
            return ResumeCreated(id=resume_id)
        check_quota(conn, settings, principal, "upload")
        ensure_queue_capacity(conn, settings)
        try:
            check_artifact_capacity(conn, settings, "design")
        except ArtifactQueueFull as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After": "30"}) from exc
        conn.execute(
            """
            INSERT INTO resumes (id, workspace_id, created_at, filename, pdf_sha256, status, upload_pdf)
            VALUES (?, ?, ?, ?, ?, 'queued', ?)
        """,
            (
                resume_id,
                principal.workspace_id,
                datetime.now(UTC).isoformat(timespec="seconds"),
                filename,
                digest,
                data,
            ),
        )
        enqueue(conn, resume_id)
        conn.commit()
    finally:
        conn.close()
    return ResumeCreated(id=resume_id)


def read_resume(principal: Principal, resume_id: str, settings: Settings) -> Resume:
    row = fetch_resume(resume_id, settings, principal=principal)
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


def read_yaml(principal: Principal, resume_id: str, settings: Settings) -> str:
    row = fetch_resume(resume_id, settings, principal=principal)
    if not row["rendercv_yaml"]:
        raise HTTPException(409, "This résumé hasn't been parsed yet.")
    return row["rendercv_yaml"]


def read_matches(
    principal: Principal,
    resume_id: str,
    settings: Settings,
    *,
    limit: int = 100,
    offset: int = 0,
    nearby: bool = False,
    include_closed: bool = False,
    category: MatchCategory = "all",
) -> MatchResults:
    check_page(limit, offset, maximum=500)
    if category not in {"all", "good", "other"}:
        raise HTTPException(422, "Choose all, good, or other matches.")
    conn = connect_app(settings.app_db)
    try:
        conn.execute("BEGIN")
        resume = fetch_resume(resume_id, settings, principal=principal, connection=conn)
        effective_location, location_source = location_context(resume)
        known = effective_location is not None
        prepare_match_catalog(conn, resume_id, resume["current_generation"], settings)
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
        # Category totals describe the entire visible location-filtered set, not
        # only this page. The exact threshold belongs to Other jobs.
        category_counts = conn.execute(
            f"SELECT COUNT(*) AS total, COALESCE(SUM(m.score > ?), 0) AS good_total {filtered}",
            [GOOD_MATCH_THRESHOLD, *params],
        ).fetchone()
        good_total = category_counts["good_total"]
        other_total = category_counts["total"] - good_total
        total = category_counts["total"]
        if category != "all":
            filtered += (
                " AND m.score > ?" if category == "good" else " AND m.score <= ?"
            )
            params.append(GOOD_MATCH_THRESHOLD)
            total = good_total if category == "good" else other_total
        rows = conn.execute(
            f"""
            SELECT m.*, {status} AS catalog_status, c.source_status, c.source_verified_at, c.source_stale {filtered}
            ORDER BY {MATCH_ORDER} LIMIT ? OFFSET ?
        """,
            [*params, limit, offset],
        ).fetchall()
    finally:
        conn.close()
    return MatchResults(
        items=[match_for_display(row, known) for row in rows],
        total=total,
        good_total=good_total,
        other_total=other_total,
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


def good_links(
    principal: Principal, resume_id: str, settings: Settings
) -> GoodMatchLinks:
    """All available good links, in rank order, from one selected-result snapshot."""
    conn = connect_app(settings.app_db)
    try:
        conn.execute("BEGIN")
        resume = fetch_resume(resume_id, settings, principal=principal, connection=conn)
        prepare_match_catalog(conn, resume_id, resume["current_generation"], settings)
        rows = conn.execute(
            f"""
            SELECT m.job_snapshot FROM matches m
            JOIN match_catalog c ON c.job_key = m.job_key
            WHERE m.resume_id = ? AND m.generation = ? AND m.score > ?
                AND c.catalog_status != 'closed'
            ORDER BY {MATCH_ORDER}
        """,
            (resume_id, resume["current_generation"], GOOD_MATCH_THRESHOLD),
        )
        # Dict keys retain first occurrence, so duplicates keep their highest rank.
        links: dict[str, None] = {}
        for row in rows:
            job = json.loads(row["job_snapshot"]) if row["job_snapshot"] else {}
            if url := job_url(job):
                links.setdefault(url, None)
        return GoodMatchLinks(links=list(links))
    finally:
        conn.close()


def read_savings(
    principal: Principal, resume_id: str, settings: Settings
) -> TimeSavings:
    conn = connect_app(settings.app_db)
    try:
        conn.execute("BEGIN")
        fetch_resume(resume_id, settings, principal=principal, connection=conn)
        result = time_savings(conn, resume_id)
        return result
    finally:
        conn.close()


def retry_resume(
    principal: Principal, resume_id: str, settings: Settings
) -> ResumeCreated:
    row = fetch_resume(resume_id, settings, principal=principal)
    if not row["rendercv_yaml"] and not has_upload(resume_id, settings):
        raise HTTPException(
            409, "The original PDF is unavailable. Please upload it again."
        )
    conn = connect_app(settings.app_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        authorize_resume(conn, resume_id, principal)
        ensure_not_running(conn, resume_id)
        check_quota(conn, settings, principal, "retry")
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


def rematch_resume(
    principal: Principal,
    resume_id: str,
    settings: Settings,
    *,
    location: str | None = None,
) -> ResumeCreated:
    row = fetch_resume(resume_id, settings, principal=principal)
    if not row["rendercv_yaml"]:
        raise HTTPException(409, "Wait for résumé parsing to finish before rematching.")
    yaml = row["rendercv_yaml"]
    revision = row["candidate_revision"]
    if location is not None:
        location = location.strip()
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
        authorize_resume(conn, resume_id, principal)
        ensure_not_running(conn, resume_id)
        check_quota(conn, settings, principal, "rematch")
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


def delete_resume(principal: Principal, resume_id: str, settings: Settings) -> None:
    conn = connect_app(settings.app_db)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            authorize_resume(conn, resume_id, principal)
            # Retire the ID atomically with the data. A delayed upload retry must
            # never resurrect a CV deleted after its first accepted response.
            conn.execute(
                "INSERT INTO deleted_resumes(resume_id, deleted_at) VALUES (?, ?)",
                (resume_id, datetime.now(UTC).isoformat(timespec="seconds")),
            )
            deleted = conn.execute("DELETE FROM resumes WHERE id = ?", (resume_id,))
            if not deleted.rowcount:
                raise HTTPException(404, "Résumé not found.")
    finally:
        conn.close()


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
    resume_id: str,
    settings: Settings,
    *,
    principal: Principal,
    connection: sqlite3.Connection | None = None,
) -> Any:
    conn = connection or connect_app(settings.app_db)
    try:
        authorize_resume(conn, resume_id, principal)
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


def prepare_match_catalog(
    conn: sqlite3.Connection, resume_id: str, generation: int, settings: Settings
) -> None:
    """Resolve all grouped identities once, before any filtering or pagination."""
    states = catalog_states(conn, resume_id, generation, settings)
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


def match_for_display(row: Any, location_known: bool) -> Match:
    job = json.loads(row["job_snapshot"]) if row["job_snapshot"] else {}
    return Match(
        job_id=row["job_id"],
        job_key=row["job_key"],
        title=job.get("title", "Saved job (original details unavailable)"),
        company=job.get("company"),
        locations=json.loads(row["locations"]),
        workplace_type=job.get("workplace_type"),
        url=job_url(job),
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


class ResumeSummary(BaseModel):
    id: str
    filename: str | None
    status: str
    created_at: str
    good_total: int


class ResumeList(BaseModel):
    items: list[ResumeSummary]
    total: int
    offset: int
    limit: int
    has_more: bool


def check_page(limit: int, offset: int, *, maximum: int = 100) -> None:
    if not 1 <= limit <= maximum or offset < 0:
        raise HTTPException(
            422, f"Use a limit of 1–{maximum} and a nonnegative offset."
        )


def list_resumes(
    principal: Principal, settings: Settings, *, limit: int = 50, offset: int = 0
) -> ResumeList:
    check_page(limit, offset)
    conn = connect_app(settings.app_db)
    try:
        conn.execute("BEGIN")
        total = conn.execute(
            "SELECT COUNT(*) FROM resumes WHERE workspace_id = ?",
            (principal.workspace_id,),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT id,filename,status,created_at,current_generation FROM resumes WHERE workspace_id = ? ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
            (principal.workspace_id, limit, offset),
        ).fetchall()
        items = []
        for row in rows:
            prepare_match_catalog(conn, row["id"], row["current_generation"], settings)
            good_total = conn.execute(
                "SELECT COUNT(*) FROM matches m JOIN match_catalog c ON c.job_key=m.job_key WHERE m.resume_id=? AND m.generation=? AND m.score>? AND c.catalog_status!='closed'",
                (row["id"], row["current_generation"], GOOD_MATCH_THRESHOLD),
            ).fetchone()[0]
            conn.execute("DROP TABLE match_catalog")
            items.append(
                ResumeSummary(
                    id=row["id"],
                    filename=row["filename"],
                    status=row["status"],
                    created_at=row["created_at"],
                    good_total=good_total,
                )
            )
    finally:
        conn.close()
    return ResumeList(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
        has_more=offset + len(items) < total,
    )


class TailoredCV(BaseModel):
    id: str
    status: Literal[
        "queued", "waiting_for_design", "tailoring", "rendering", "ready", "failed"
    ]
    error: str | None
    download_url: str | None
    design_source: Literal["reconstructed", "default"] | None


class TailoredSummary(TailoredCV):
    job_key: str
    created_at: str
    updated_at: str


class TailoredList(BaseModel):
    items: list[TailoredSummary]
    total: int
    offset: int
    limit: int
    has_more: bool


def tailored_model(row: Any) -> TailoredCV:
    return TailoredCV(
        id=row["id"],
        status=row["status"],
        error=row["error"],
        design_source=row["design_source"],
        download_url=f"/resumes/{row['resume_id']}/tailored/{row['id']}/cv.pdf"
        if row["status"] == "ready"
        else None,
    )


def create_tailored(
    principal: Principal, resume_id: str, job_key: str, settings: Settings
) -> TailoredCV:
    from .artifact_store import request_tailoring

    conn = connect_app(settings.app_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        authorize_resume(conn, resume_id, principal)
        # Store deduplicates before queuing; charge only when work is actually
        # inserted/retried, and roll the whole transaction back if over budget.
        before = conn.execute(
            "SELECT id,status FROM tailored_cvs WHERE resume_id=?", (resume_id,)
        ).fetchall()
        previous = {row["id"]: row["status"] for row in before}
        result = request_tailoring(conn, resume_id, job_key, settings)
        if result["id"] not in previous or previous[result["id"]] == "failed":
            check_quota(conn, settings, principal, "tailor")
        conn.commit()
        return tailored_model(result)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ArtifactQueueFull as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "30"}) from exc
    finally:
        conn.close()


def get_tailored(
    principal: Principal, resume_id: str, artifact_id: str, settings: Settings
) -> TailoredCV:
    from .artifact_store import fetch_tailored

    conn = connect_app(settings.app_db)
    try:
        authorize_resume(conn, resume_id, principal)
        return tailored_model(fetch_tailored(conn, resume_id, artifact_id))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    finally:
        conn.close()


def list_tailored(
    principal: Principal,
    resume_id: str,
    settings: Settings,
    *,
    limit: int = 100,
    offset: int = 0,
) -> TailoredList:
    check_page(limit, offset)
    conn = connect_app(settings.app_db)
    try:
        conn.execute("BEGIN")
        authorize_resume(conn, resume_id, principal)
        total = conn.execute(
            "SELECT COUNT(*) FROM tailored_cvs WHERE resume_id=?", (resume_id,)
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT t.id,t.resume_id,t.status,t.error,t.job_key,t.created_at,t.updated_at,d.source AS design_source FROM tailored_cvs t JOIN resume_designs d ON d.id=t.design_id WHERE t.resume_id=? ORDER BY t.created_at DESC,t.id DESC LIMIT ? OFFSET ?",
            (resume_id, limit, offset),
        ).fetchall()
        items = [
            TailoredSummary(
                **tailored_model(row).model_dump(),
                job_key=row["job_key"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]
    finally:
        conn.close()
    return TailoredList(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
        has_more=offset + len(items) < total,
    )


def read_tailored_pdf(
    principal: Principal, resume_id: str, artifact_id: str, settings: Settings
) -> tuple[bytes, str]:
    conn = connect_app(settings.app_db)
    try:
        authorize_resume(conn, resume_id, principal)
        row = conn.execute(
            "SELECT status,pdf_bytes,filename FROM tailored_cvs WHERE id=? AND resume_id=?",
            (artifact_id, resume_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, "Tailored CV not found.")
    if row["status"] != "ready" or not row["pdf_bytes"]:
        raise HTTPException(409, "This tailored CV is still being prepared.")
    return row["pdf_bytes"], row["filename"] or "tailored-cv.pdf"


def get_design(principal: Principal, resume_id: str, settings: Settings) -> dict:
    from .artifact_store import design_status

    conn = connect_app(settings.app_db)
    try:
        authorize_resume(conn, resume_id, principal)
        return design_status(conn, resume_id) or {
            "status": "not_requested",
            "source": None,
        }
    finally:
        conn.close()
