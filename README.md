# AviCV

**Find jobs that fit your CV.** Try it at [avicv.com](https://avicv.com).

Upload your CV as a PDF. AviCV searches a catalog of open jobs, then uses TypeSafe’s Jev to compare relevant job descriptions with your experience and rank your matches. The catalog focuses on US, Canadian, and remote roles.

From your results, you can:

- **Apply:** open the job’s application page.
- **Copy links:** copy one application link or all links under **Good matches**.
- **Tailor your CV:** download a job-specific PDF that emphasizes your relevant experience, with factual checks and a design based on your original CV.

Spend less time searching, checking job requirements, and preparing each application. Your results and progress survive page refreshes.

## Tech stack

- **Frontend:** SvelteKit, Svelte, and Bun.
- **Backend:** FastAPI, Python, and SQLite.
- **Job matching:** TypeSafe’s Jev.
- **CV parsing and tailoring:** Gemini through OpenRouter, with RenderCV for PDF generation.

## Run locally

You need [Bun](https://bun.com) 1.4+ and [uv](https://docs.astral.sh/uv/). uv installs Python 3.14 if needed.

From the repository root:

```sh
bun install
uv sync
cp backend/.env.example backend/.env
```

Add `OPENROUTER_API_KEY` and `TYPESAFE_API_KEY` to `backend/.env`, then start the app:

```sh
bun run dev
```

This starts the frontend, API, and job sync. Open the [app](http://localhost:5173) or the [API docs](http://127.0.0.1:8000/docs).

The first launch downloads job listings automatically. Matching starts once jobs are available. Jobs, uploaded CVs, and results are stored in `backend/data/`; browser history and preferences use localStorage.

## Commands

Run these from the repository root:

| Command | Purpose |
| --- | --- |
| `bun run dev` | Start the frontend, API, and job sync |
| `bun run jobs:sync` | Refresh job listings now |
| `bun run check` | Check frontend types; run backend lint, formatting checks, type checks, and tests |
| `bun --cwd=frontend run test` | Run frontend unit tests |
| `bun run build` | Build the frontend for production |

See the [frontend README](frontend/README.md) and [backend README](backend/README.md) for configuration, architecture, and deployment details.
