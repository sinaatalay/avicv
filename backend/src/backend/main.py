import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.routing import Mount

from . import auth, transfers
from .config import get_settings
from .db import connect_app
from .jobs import router as jobs
from .logging_utils import install_access_log_redaction
from .mcp_server import build_mcp_app, mcp
from .resumes import artifact_router
from .resumes import router as resumes
from .resumes.artifact_queue import artifact_worker
from .resumes.queue import recover_legacy, worker
from .resumes.storage import backfill_snapshots


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    install_access_log_redaction()
    # The SDK's transport manager is single-use. A fresh transport also supports
    # repeated ASGI lifespans (tests/restarts) without retaining session state.
    mcp_route.app = build_mcp_app()
    settings = get_settings()
    connect_app(settings.app_db).close()
    backfill_snapshots(settings)
    recover_legacy(settings)
    tasks = [asyncio.create_task(worker(settings), name="resume-worker")]
    if settings.artifact_workers_enabled:
        tasks.extend(
            [
                asyncio.create_task(
                    artifact_worker(settings, "design"), name="cv-design-worker"
                ),
                asyncio.create_task(
                    artifact_worker(settings, "tailor"), name="cv-tailor-worker"
                ),
            ]
        )
    async with mcp.session_manager.run():
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="AviCV API", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(transfers.router)
app.include_router(resumes.router)
app.include_router(artifact_router.router)
app.include_router(jobs.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# Mount last: the SDK's default /mcp endpoint stays exactly /mcp, without redirects.
mcp_route = Mount("/", app=build_mcp_app())
app.router.routes.append(mcp_route)
