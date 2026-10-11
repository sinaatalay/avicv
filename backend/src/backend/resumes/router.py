"""Browser REST transport for the shared, ownership-checked résumé service."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Response, UploadFile
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from ..auth import PrincipalDep
from ..config import Settings, get_settings
from . import service
from .savings import TimeSavings
from .service import (
    GoodMatchLinks,
    Match,
    MatchCategory,
    MatchResults,
    RematchRequest,
    Resume,
    ResumeCreated,
    ResumeList,
)

router = APIRouter(prefix="/resumes", tags=["resumes"])
SettingsDep = Annotated[Settings, Depends(get_settings)]
PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
}


def private_response(response: Response) -> None:
    response.headers.update(PRIVATE_HEADERS)


router.dependencies.append(Depends(private_response))


@router.post("", status_code=202)
async def upload_resume(
    file: UploadFile, principal: PrincipalDep, settings: SettingsDep
) -> ResumeCreated:
    data = await file.read(service.MAX_BYTES + 1)
    return await run_in_threadpool(
        service.create_resume, principal, data, file.filename, settings
    )


@router.get("")
def list_resumes(
    principal: PrincipalDep,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ResumeList:
    return service.list_resumes(principal, settings, limit=limit, offset=offset)


@router.get("/{resume_id}")
def get_resume(
    resume_id: str, principal: PrincipalDep, settings: SettingsDep
) -> Resume:
    return service.read_resume(principal, resume_id, settings)


@router.get("/{resume_id}/rendercv.yaml", response_class=PlainTextResponse)
def get_rendercv_yaml(
    resume_id: str, principal: PrincipalDep, settings: SettingsDep
) -> PlainTextResponse:
    return PlainTextResponse(
        service.read_yaml(principal, resume_id, settings),
        media_type="application/yaml",
        headers=PRIVATE_HEADERS
        | {"Content-Disposition": 'attachment; filename="cv.yaml"'},
    )


@router.get("/{resume_id}/match-results")
def get_match_results(
    resume_id: str,
    principal: PrincipalDep,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    nearby: bool = False,
    include_closed: bool = False,
    category: MatchCategory = "all",
) -> MatchResults:
    return service.read_matches(
        principal,
        resume_id,
        settings,
        limit=limit,
        offset=offset,
        nearby=nearby,
        include_closed=include_closed,
        category=category,
    )


@router.get("/{resume_id}/good-match-links")
def get_good_match_links(
    resume_id: str, principal: PrincipalDep, settings: SettingsDep
) -> GoodMatchLinks:
    return service.good_links(principal, resume_id, settings)


@router.get("/{resume_id}/time-savings")
def get_time_savings(
    resume_id: str, principal: PrincipalDep, settings: SettingsDep
) -> TimeSavings:
    return service.read_savings(principal, resume_id, settings)


@router.get("/{resume_id}/matches")
def get_matches(
    resume_id: str,
    principal: PrincipalDep,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    nearby: bool = False,
    category: MatchCategory = "all",
) -> list[Match]:
    return service.read_matches(
        principal,
        resume_id,
        settings,
        limit=limit,
        offset=offset,
        nearby=nearby,
        category=category,
    ).items


@router.post("/{resume_id}/retry", status_code=202)
def retry_resume(
    resume_id: str, principal: PrincipalDep, settings: SettingsDep
) -> ResumeCreated:
    return service.retry_resume(principal, resume_id, settings)


@router.post("/{resume_id}/rematch", status_code=202)
def rematch_resume(
    resume_id: str,
    principal: PrincipalDep,
    settings: SettingsDep,
    request: Annotated[RematchRequest | None, Body()] = None,
) -> ResumeCreated:
    return service.rematch_resume(
        principal, resume_id, settings, location=request.location if request else None
    )


@router.delete("/{resume_id}", status_code=204)
def delete_resume(
    resume_id: str, principal: PrincipalDep, settings: SettingsDep
) -> Response:
    service.delete_resume(principal, resume_id, settings)
    return Response(status_code=204, headers=PRIVATE_HEADERS)
