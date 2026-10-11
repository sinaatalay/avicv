# AviCV

Find jobs that fit your CV. Apply directly and download a tailored résumé. Built for [avicv.com](https://avicv.com).

Drop your résumé as a PDF and see which open jobs you'd fit. The backend reads the PDF into a [RenderCV](https://docs.rendercv.com) CV with Gemini, compares it with thousands of open US/Canada roles pulled from employers' public job boards, and ranks them with [Jev](https://docs.typesafe.ai), TypeSafe's decision model. The details are in [backend/README.md](backend/README.md).

A SvelteKit frontend and a FastAPI backend live in one repository.

```
avicv/
├ frontend/          SvelteKit 3 + Svelte 5 + Vite 8 app, run with Bun   → frontend/README.md
├ backend/           FastAPI app on Python 3.14, managed with uv         → backend/README.md
├ package.json       Bun workspace root, plus scripts that drive both apps
├ bun.lock
├ pyproject.toml     uv workspace root (virtual: no [project] table)
├ uv.lock
└ .python-version    3.14
```

Each toolchain has its own workspace rooted here, so there is one lockfile per language and one install command each. This mirrors the FastAPI team's [full-stack template](https://github.com/fastapi/full-stack-fastapi-template). To add a package later (say, a shared Python library or a generated TypeScript API client), put it next to `frontend/` and `backend/` and list it under `workspaces` in `package.json` or `members` in `pyproject.toml`.

## Getting started

You need [Bun](https://bun.com) 1.4+ and [uv](https://docs.astral.sh/uv/). uv downloads Python 3.14 itself if needed.

```sh
bun install                               # frontend dependencies
uv sync                                   # backend dependencies, into .venv
cp backend/.env.example backend/.env      # then add your OpenRouter and TypeSafe keys
bun run dev                               # frontend, API and job sync
```

- SvelteKit: http://localhost:5173
- FastAPI: http://127.0.0.1:8000, with interactive API docs at http://127.0.0.1:8000/docs

The first job sync writes `backend/data/jobs.db`. Complete syncs refresh every 6 hours; incomplete sources remain eligible for retry. Résumés wait for the first usable catalog automatically. Uploads, progress, and complete results persist in `backend/data/app.db`; browser history and view preferences also survive refresh through localStorage.

## Scripts

| Command                                        | What it does                                       |
| ---------------------------------------------- | -------------------------------------------------- |
| `bun run dev`                                  | Run the frontend, the API and the job sync in parallel |
| `bun run dev:frontend`, `bun run dev:backend`, `bun run dev:jobs` | Run one of them                     |
| `bun run jobs:sync`                            | Pull job postings now, even if the last sync is recent |
| `bun run check`                                | svelte-check, then ruff, ty and pytest             |
| `bun run build`                                | Production build of the frontend (`frontend/build`) |

## How the frontend talks to the backend

The browser only ever talks to SvelteKit. SvelteKit [remote functions](https://svelte.dev/docs/kit/remote-functions) (`query`, `form` and `command`, in `*.remote.ts` files) run on the SvelteKit server and call FastAPI over HTTP through the `api()` helper in [`frontend/src/lib/server/api.ts`](frontend/src/lib/server/api.ts):

```
browser ──remote functions──▶ SvelteKit server (Bun) ──HTTP──▶ FastAPI (uvicorn)
```

- The backend's base URL comes from `API_URL`, declared in [`frontend/src/env.ts`](frontend/src/env.ts). It defaults to `http://127.0.0.1:8000`, so local development needs no configuration.
- Because requests are server-to-server, FastAPI needs no CORS setup and doesn't have to be publicly reachable.

## Deploying

The frontend and backend deploy as two services:

- **Backend:** `uv run fastapi run` in `backend/` (listens on `0.0.0.0:8000`), with the keys from `backend/.env.example` set. Also run `uv run jobs-ingest --loop` (or `uv run jobs-ingest` from cron) on the same disk, since it writes the jobs database the API reads.
- **Frontend:** `bun run build`, then `bun ./build` in `frontend/` (listens on port 3000) with `API_URL` pointing at the backend and `BODY_SIZE_LIMIT=12M` to accommodate a 10 MiB PDF plus multipart overhead. The Bun server assumes it sits behind an HTTPS proxy, so form submissions over plain HTTP fail its CSRF check. To try the production build locally, use `bun run preview` in `frontend/`, or set [`paths.origin`](https://svelte.dev/docs/kit/@sveltejs-kit-vite#paths).

Résumé links act as bearer access in this trusted/local app. Add authentication and per-user ownership before making a multi-user public deployment. Keep both SQLite databases on persistent storage.
