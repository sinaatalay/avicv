# AviCV

**Find jobs that fit your CV — in your browser or your coding agent.** Try it at [avicv.com](https://avicv.com).

Upload your CV as a PDF. AviCV searches a catalog of open jobs, then uses TypeSafe’s Jev to compare relevant job descriptions with your experience and rank your matches. The catalog focuses on US, Canadian, and remote roles.

From your results, you can:

- **Apply:** open the job’s application page.
- **Copy links:** copy one application link or all links under **Good matches**.
- **Tailor your CV:** download a job-specific PDF that emphasizes your relevant experience, with factual checks and a design based on your original CV.

Upload in the browser and continue in your favorite coding agent, or the other way around. Both clients use the same saved CVs, matches, and tailored PDFs. Open **Connect an agent** in AviCV, create a connection key, and copy the setup instructions. The MCP endpoint is `https://avicv.com/mcp`.

The website also includes **Ask AviCV**, a CopilotKit conversation with access to the same tools and Darwin agent discovery. Your CV processing and saved results survive page refreshes and server restarts.

## Tech stack

- **Frontend:** SvelteKit, Svelte, and Bun.
- **Backend:** FastAPI, Python, and SQLite.
- **Job matching:** TypeSafe’s Jev.
- **CV parsing and tailoring:** Gemini through OpenRouter, with [RenderCV](https://github.com/rendercv/rendercv) for design and PDF generation.
- **Agent experience:** FastAPI MCP, CopilotKit, and Darwin, integrated from [speedrun-amber](https://github.com/ziadgit/speedrun-amber).

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

This starts the website, API/MCP, conversational agent, and job sync. Open the [app](http://localhost:5173) or the [API docs](http://127.0.0.1:8000/docs). The chat uses the same OpenRouter configuration by default; [Anthropic is optional](agent/README.md).

The first launch downloads job listings automatically. Matching starts once jobs are available. Jobs, CVs, results, credentials, and PDFs are stored in `backend/data/`; browser previews and chat history use localStorage. A browser cookie and revocable agent keys identify the same private workspace. Keep the cookie or a connection key: this experimental version has no account recovery.

## Deploy

Configure `backend/.env`, then run:

```sh
APP_ORIGIN=https://avicv.com docker compose up --build -d
```

Route your HTTPS domain to port **8080**. The website, `/mcp`, chat, and file downloads share that domain. Docker Compose keeps data in a persistent volume. `APP_ORIGIN` must match during build and startup; rebuild when changing domains. See [deployment instructions](DEPLOYMENT.md), including restoring CVs from the older ownerless database.

## Commands

Run these from the repository root:

| Command | Purpose |
| --- | --- |
| `bun run dev` | Start all services locally |
| `bun run jobs:sync` | Refresh job listings now |
| `bun run check` | Run type checks, lint, and backend/agent tests |
| `bun --cwd=frontend run test` | Run frontend unit tests |
| `bun run build` | Build the website and chat for production |
| `bun run start` | Start all production services on port 8080 |

See the [frontend](frontend/README.md), [backend](backend/README.md), and [agent](agent/README.md) READMEs for implementation details.
