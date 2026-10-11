import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .db import connect_app
from .jobs import router as jobs
from .resumes import router as resumes
from .resumes.queue import recover_legacy, worker
from .resumes.storage import backfill_snapshots


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = get_settings()
    connect_app(settings.app_db).close()
    backfill_snapshots(settings)
    recover_legacy(settings)
    task = asyncio.create_task(worker(settings), name="resume-worker")
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


app = FastAPI(title="speedhack API", lifespan=lifespan)
app.include_router(resumes.router)
app.include_router(jobs.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
