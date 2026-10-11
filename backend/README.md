# backend

A vanilla [FastAPI](https://fastapi.tiangolo.com) app on Python 3.14, managed with [uv](https://docs.astral.sh/uv/). It is a member of the uv workspace rooted at the repository root, so `uv.lock`, `.python-version` and `.venv` live there.

## Developing

```sh
uv sync              # from anywhere in the repo: install the backend and its dev tools
uv run fastapi dev   # from backend/: http://127.0.0.1:8000, docs at /docs
```

`fastapi dev` finds the app through `[tool.fastapi] entrypoint` in `pyproject.toml`, so it takes no arguments. In production, use `uv run fastapi run`.

## Checks

```sh
uv run pytest        # tests (FastAPI's TestClient, backed by httpx2)
uv run ruff check    # lint
uv run ruff format   # format
uv run ty check      # type-check
```

## Layout

```
src/backend/
├ main.py            the FastAPI app: routers and /health
└ routers/
  └ items.py         example /items resource (in-memory storage)
tests/               pytest tests
pyproject.toml       dependencies and tool config (fastapi, pytest, ruff)
```

This is uv's default packaged layout (`src/<package>/` with the `uv_build` backend). The package is installed into the environment, so tests import it as `backend` without any path tweaks.
