# Deploy AviCV

One deployment serves the website, FastAPI MCP, and CopilotKit chat. The website is the only public service; the API and conversational agent listen on loopback. RenderCV runs in the backend, and background workers resume unfinished CV work after restarts.

## Docker Compose

1. Copy `backend/.env.example` to `backend/.env` and set `OPENROUTER_API_KEY` and `TYPESAFE_API_KEY`. Keep the file private. OpenRouter BYOK must be enabled for that OpenRouter key and the selected provider. Chat reuses OpenRouter unless you explicitly configure an Anthropic model/key.
2. Run `APP_ORIGIN=https://avicv.com docker compose up --build -d`.
3. Point your HTTPS reverse proxy at port **8080**. Preserve the public Host header, permit uploads of at least 12 MiB, and disable response buffering for streamed chat/MCP responses. Do not cache `/mcp`, `/connect`, `/resumes`, `/uploads`, `/downloads`, `/chat`, or `/api/copilotkit`. Disable or redact access logs for `/downloads/*`: those paths contain temporary download credentials.
4. Check `/healthz`, open the website, and create a connection from **Connect an agent**. Copy the instructions for your client, then ask it to list your saved CVs.

`APP_ORIGIN` is used at **build time and runtime**, including SvelteKit's CSRF checks. Compose supplies it to both. Rebuild when you change the domain. For an HTTP localhost trial, omit `APP_ORIGIN` and open `http://localhost:8080`.

The `avicv-data` volume holds both SQLite databases, uploaded PDFs, generated artifacts, credentials, and durable queues. Preserve and back it up; do not run `docker compose down -v` unless you intend to erase those records. Use a SQLite-aware backup or stop the service before copying the volume. The image excludes `.env`, local CVs, and development data.

Run **one application container** with this volume. Multiple hosts need shared database/object storage and distributed conversation admission before horizontal scaling. The current design intentionally keeps one durable SQLite owner of the data.

## Without Docker

Install Bun 1.4+ and uv, run `bun install --frozen-lockfile` and `uv sync --frozen`, and configure `backend/.env`. Then:

```sh
APP_ORIGIN=https://avicv.com bun run build
APP_ORIGIN=https://avicv.com bun run start
```

`PORT` defaults to 8080; `API_PORT` to 8000; `AGENT_PORT` to 4000. `DATA_DIR` accepts an absolute path or a path relative to `backend/`. `JOBS_SYNC_ENABLED=false` disables automatic catalog refresh. The launcher stops all services if one fails so the host can restart the deployment. Use a process supervisor when deploying without Docker.

## Shared access and existing CVs

The browser receives an HttpOnly workspace cookie. Agent keys belong to that workspace, are shown only once, and can be revoked from `/connect`. CVs and artifacts are saved on the server; localStorage is a browser cache, not the authorization boundary. Removing a browser history entry hides it locally; it does not delete the server record. Clearing cookies loses browser access to that workspace; this version has no sign-in or account recovery.

Existing CVs from before workspace ownership are preserved but private until the administrator explicitly assigns them. On a local installation, create an agent key in the intended browser workspace, then run:

```sh
uv run --directory backend avicv-claim-legacy --resume RESUME_ID
# For a known single-user database only:
uv run --directory backend avicv-claim-legacy --all-unowned
```

The command prompts for the connection key without echoing it. It cannot take another workspace's CVs. For Docker, run `.venv/bin/avicv-claim-legacy` with the same arguments through `docker compose exec avicv`.

Uploads, tailoring, retries, and chat have persistent per-workspace and global daily limits; configure them in `backend/.env`. Invalid, foreign, or revoked credentials cannot access CVs. Temporary upload/download grants expire after ten minutes and stop working when their issuing credential is revoked.
