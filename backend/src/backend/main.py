from fastapi import FastAPI

from .routers import items

app = FastAPI(title="speedhack API")

app.include_router(items.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
