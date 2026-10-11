# backend

A vanilla [FastAPI](https://fastapi.tiangolo.com) app on Python 3.14, managed with [uv](https://docs.astral.sh/uv/). It is a member of the uv workspace rooted at the repository root, so `uv.lock`, `.python-version` and `.venv` live there.

## Developing

```sh
uv sync              # from anywhere in the repo: install the backend and its dev tools
uv run fastapi dev   # from backend/: http://127.0.0.1:8000, docs at /docs
```

`fastapi dev` finds the app through `[tool.fastapi] entrypoint` in `pyproject.toml`, so it takes no arguments. `bun run dev` at the repository root starts it together with the frontend. In production, use `uv run fastapi run`.

## Checks

```sh
uv run pytest        # tests (FastAPI's TestClient, backed by httpx2)
uv run ruff check    # lint
uv run ruff format   # format
uv run ty check      # type-check
```

`bun run check` at the repository root runs all of these after the frontend's checks.

## API

| Method | Path      | Description                        |
| ------ | --------- | ---------------------------------- |
| `GET`  | `/health` | Liveness check: `{"status": "ok"}` |

## Layout

```
src/backend/
└ main.py            the FastAPI app
tests/               pytest tests
pyproject.toml       dependencies and tool config (fastapi, pytest, ruff)
```

This is uv's default packaged layout (`src/<package>/` with the `uv_build` backend). The package is installed into the environment, so tests import it as `backend` without any path tweaks.
