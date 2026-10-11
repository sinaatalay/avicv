from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .db import connect_app
from .jobs import router as jobs
from .resumes import router as resumes


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    connect_app(get_settings().app_db).close()  # creates app.db and its tables
    yield


app = FastAPI(title="speedhack API", lifespan=lifespan)

app.include_router(resumes.router)
app.include_router(jobs.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
