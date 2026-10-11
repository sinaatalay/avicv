# speedhack

Monorepo for the speedhack web app.

| Directory                | What                                     | Toolchain  |
| ------------------------ | ---------------------------------------- | ---------- |
| [`frontend/`](frontend/) | SvelteKit 3 + Svelte 5 + Vite 8 web app  | Bun        |
| [`backend/`](backend/)   | FastAPI app on Python 3.14               | uv         |

The root is a workspace for both toolchains: `package.json` + `bun.lock` for Bun, and `pyproject.toml` + `uv.lock` + `.python-version` for uv.

```sh
bun install    # frontend dependencies
uv sync        # backend dependencies (into .venv)

bun run dev                      # SvelteKit dev server on http://localhost:5173
cd backend && uv run fastapi dev # FastAPI dev server on http://127.0.0.1:8000
```
