"""Browser tailoring transport; ownership and state transitions live in service."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from ..auth import PrincipalDep
from ..config import Settings, get_settings
from . import service
from .router import PRIVATE_HEADERS, private_response
from .service import TailoredCV, TailoredList

router = APIRouter(
    prefix="/resumes", tags=["tailored CVs"], dependencies=[Depends(private_response)]
)
SettingsDep = Annotated[Settings, Depends(get_settings)]


class TailorRequest(BaseModel):
    job_key: str = Field(min_length=1, max_length=256)


@router.post("/{resume_id}/tailor", status_code=202)
def create_tailored_cv(
    resume_id: str,
    request: TailorRequest,
    principal: PrincipalDep,
    settings: SettingsDep,
) -> TailoredCV:
    return service.create_tailored(principal, resume_id, request.job_key, settings)


@router.get("/{resume_id}/tailored")
def list_tailored_cvs(
    resume_id: str,
    principal: PrincipalDep,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TailoredList:
    return service.list_tailored(
        principal, resume_id, settings, limit=limit, offset=offset
    )


@router.get("/{resume_id}/tailored/{artifact_id}")
def get_tailored_cv(
    resume_id: str, artifact_id: str, principal: PrincipalDep, settings: SettingsDep
) -> TailoredCV:
    return service.get_tailored(principal, resume_id, artifact_id, settings)


@router.get("/{resume_id}/tailored/{artifact_id}/cv.pdf")
def download_tailored_cv(
    resume_id: str, artifact_id: str, principal: PrincipalDep, settings: SettingsDep
) -> Response:
    data, filename = service.read_tailored_pdf(
        principal, resume_id, artifact_id, settings
    )
    return Response(
        content=data,
        media_type="application/pdf",
        headers=PRIVATE_HEADERS
        | {
            "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename, safe='')}"
        },
    )


@router.get("/{resume_id}/design")
def get_design_status(
    resume_id: str, principal: PrincipalDep, settings: SettingsDep
) -> dict:
    return service.get_design(principal, resume_id, settings)
