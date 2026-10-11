"""Tailored CV creation, polling and private PDF downloads."""

from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..db import connect_app
from .artifact_store import (
    ArtifactQueueFull,
    design_status,
    fetch_tailored,
    request_tailoring,
)

router = APIRouter(prefix="/resumes", tags=["tailored CVs"])
SettingsDep = Annotated[Settings, Depends(get_settings)]
PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
}


class TailorRequest(BaseModel):
    job_key: str = Field(min_length=1, max_length=256)


class TailoredCV(BaseModel):
    id: str
    status: Literal[
        "queued", "waiting_for_design", "tailoring", "rendering", "ready", "failed"
    ]
    error: str | None
    download_url: str | None
    design_source: Literal["reconstructed", "default"] | None


def response_model(row) -> TailoredCV:
    return TailoredCV(
        id=row["id"],
        status=row["status"],
        error=row["error"],
        design_source=row["design_source"],
        download_url=f"/resumes/{row['resume_id']}/tailored/{row['id']}/cv.pdf"
        if row["status"] == "ready"
        else None,
    )


@router.post("/{resume_id}/tailor", status_code=202)
def create_tailored_cv(
    resume_id: str, request: TailorRequest, settings: SettingsDep, response: Response
) -> TailoredCV:
    response.headers.update(PRIVATE_HEADERS)
    conn = connect_app(settings.app_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = request_tailoring(conn, resume_id, request.job_key, settings)
        conn.commit()
        return response_model(result)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ArtifactQueueFull as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "30"}) from exc
    finally:
        conn.close()


@router.get("/{resume_id}/tailored/{artifact_id}")
def get_tailored_cv(
    resume_id: str, artifact_id: str, settings: SettingsDep, response: Response
) -> TailoredCV:
    response.headers.update(PRIVATE_HEADERS)
    conn = connect_app(settings.app_db)
    try:
        return response_model(fetch_tailored(conn, resume_id, artifact_id))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    finally:
        conn.close()


@router.get("/{resume_id}/tailored/{artifact_id}/cv.pdf")
def download_tailored_cv(
    resume_id: str, artifact_id: str, settings: SettingsDep
) -> Response:
    conn = connect_app(settings.app_db)
    try:
        row = conn.execute(
            "SELECT status, pdf_bytes, filename FROM tailored_cvs WHERE id = ? AND resume_id = ?",
            (artifact_id, resume_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, "Tailored CV not found.")
    if row["status"] != "ready" or not row["pdf_bytes"]:
        raise HTTPException(409, "This tailored CV is still being prepared.")
    # Generated filenames contain only bounded ASCII letters/numbers/_/- and .pdf.
    filename = row["filename"] or "tailored-cv.pdf"
    return Response(
        content=row["pdf_bytes"],
        media_type="application/pdf",
        headers=PRIVATE_HEADERS
        | {
            "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename, safe='')}"
        },
    )


@router.get("/{resume_id}/design")
def get_design_status(
    resume_id: str, settings: SettingsDep, response: Response
) -> dict:
    response.headers.update(PRIVATE_HEADERS)
    conn = connect_app(settings.app_db)
    try:
        if (
            conn.execute("SELECT 1 FROM resumes WHERE id = ?", (resume_id,)).fetchone()
            is None
        ):
            raise HTTPException(404, "Résumé not found.")
        return design_status(conn, resume_id) or {
            "status": "not_requested",
            "source": None,
        }
    finally:
        conn.close()
