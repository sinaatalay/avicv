"""Résumé upload, processing status, and match results."""

import hashlib
import io
import json
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from pypdf import PdfReader

from ..config import Settings, get_settings
from ..db import connect, connect_app
from ..matching.jev import QUESTIONS_VERSION
from .parse import load_yaml
from .pipeline import process_resume

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
    """The parsed CV, shaped for display. `rendercv_yaml` holds the full RenderCV document."""

    name: str | None
    headline: str | None
    location: str | None
    sections: list[Section]


class Progress(BaseModel):
    done: int
    total: int


class Preview(BaseModel):
    """What parsing has found so far, while Gemini is still writing the CV"""

    name: str | None
    sections: list[str]


class Resume(BaseModel):
    id: str
    status: Literal["parsing", "matching", "done", "failed"]
    error: str | None
    filename: str | None
    created_at: str
    preview: Preview | None
    cv: Cv | None
    rendercv_yaml: str | None
    progress: Progress
    cost_usd: float


class Match(BaseModel):
    job_id: int
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
    location: float
    seniority: Literal["below", "matches", "above"]


@router.post("", status_code=202)
async def upload_resume(
    file: UploadFile, background_tasks: BackgroundTasks, settings: SettingsDep
) -> ResumeCreated:
    """Accept a PDF and start parsing and matching it in the background."""
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Upload a PDF smaller than 10 MB.")
    if b"%PDF-" not in data[:1024]:
        raise HTTPException(415, "Upload a PDF.")
    try:
        pages = len(PdfReader(io.BytesIO(data)).pages)
    except Exception as e:
        raise HTTPException(422, "This PDF can't be opened.") from e
    if pages > MAX_PAGES:
        raise HTTPException(422, f"Upload a résumé of at most {MAX_PAGES} pages.")

    resume_id = uuid4().hex
    conn = connect_app(settings.app_db)
    with conn:
        conn.execute(
            "INSERT INTO resumes (id, created_at, filename, pdf_sha256, status) VALUES (?, ?, ?, ?, 'parsing')",
            (
                resume_id,
                datetime.now(UTC).isoformat(timespec="seconds"),
                file.filename,
                hashlib.sha256(data).hexdigest(),
            ),
        )
    conn.close()
    background_tasks.add_task(process_resume, resume_id, data, settings)
    return ResumeCreated(id=resume_id)


@router.get("/{resume_id}")
def get_resume(resume_id: str, settings: SettingsDep) -> Resume:
    row = fetch_resume(resume_id, settings)
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
        progress=Progress(done=row["match_done"], total=row["match_total"]),
        cost_usd=(row["parse_cost_usd"] or 0) + row["match_cost_usd"],
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


@router.get("/{resume_id}/matches")
def get_matches(
    resume_id: str,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[Match]:
    """The best matches so far, best first. Fills in while the résumé is being matched."""
    fetch_resume(resume_id, settings)
    conn = connect_app(settings.app_db)
    try:
        matches = conn.execute(
            "SELECT * FROM matches WHERE resume_id = ? AND questions = ? ORDER BY score DESC LIMIT ?",
            (resume_id, QUESTIONS_VERSION, limit),
        ).fetchall()
    finally:
        conn.close()
    if not matches or not settings.jobs_db.exists():
        return []

    jobs_conn = connect(settings.jobs_db, readonly=True)
    try:
        ids = [m["job_id"] for m in matches]
        jobs = {
            row["id"]: row
            for row in jobs_conn.execute(
                "SELECT id, title, company, workplace_type, apply_url, source_url, salary_min, salary_max, "
                f"salary_currency, salary_interval, posted_at FROM jobs WHERE id IN ({', '.join('?' * len(ids))})",
                ids,
            )
        }
    finally:
        jobs_conn.close()

    return [
        Match(
            job_id=m["job_id"],
            title=job["title"],
            company=job["company"],
            locations=json.loads(m["locations"]),
            workplace_type=job["workplace_type"],
            url=job["apply_url"] or job["source_url"],
            salary_min=job["salary_min"],
            salary_max=job["salary_max"],
            salary_currency=job["salary_currency"],
            salary_interval=job["salary_interval"],
            posted_at=job["posted_at"],
            score=m["score"],
            fit=m["fit"],
            interview=m["interview"],
            must_haves=m["must_haves"],
            location=m["location"],
            seniority=m["seniority"],
        )
        for m in matches
        if (job := jobs.get(m["job_id"]))
        is not None  # skip jobs removed from jobs.db since scoring
    ]


def fetch_resume(resume_id: str, settings: Settings) -> Any:
    conn = connect_app(settings.app_db)
    try:
        row = conn.execute(
            "SELECT * FROM resumes WHERE id = ?", (resume_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, "Résumé not found.")
    return row


@lru_cache(maxsize=256)
def cv_for_display(rendercv_yaml: str) -> Cv:
    """Cached by the YAML text, since the frontend polls while matching runs."""
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
