# backend

The FastAPI app behind speedhack, on Python 3.14 and managed with [uv](https://docs.astral.sh/uv/). It is a member of the uv workspace rooted at the repository root, so `uv.lock`, `.python-version` and `.venv` live there.

It does three things:

1. **Collects job postings.** `jobs-ingest` pulls the public job boards of the employers listed in `jobs/boards.toml` (Greenhouse, Ashby, Lever and SmartRecruiters), plus remote-job searches on Himalayas, into `data/jobs.db`. It refreshes them every 6 hours.
2. **Parses résumés.** Gemini 3.8 Flash, called through OpenRouter with [Pydantic AI](https://ai.pydantic.dev), reads an uploaded PDF and writes it as [RenderCV](https://docs.rendercv.com) YAML. RenderCV validates the YAML. If validation fails, RenderCV's error messages go back to the model so it can correct them.
3. **Matches résumés to jobs.** Full-text search shortlists open US/Canada roles. Then [Jev](https://docs.typesafe.ai), TypeSafe's decision model, scores each role against the résumé.

## Setup

```sh
cp .env.example .env   # then add OPENROUTER_API_KEY and TYPESAFE_API_KEY
uv sync                # from anywhere in the repo
```

`.env.example` lists the optional settings and their defaults. You can also set any of them as an environment variable.

## Developing

```sh
uv run fastapi dev          # http://127.0.0.1:8000, docs at /docs
uv run jobs-ingest --loop   # fills data/jobs.db, then keeps it fresh
```

`bun run dev` at the repository root starts both, along with the frontend. The first job sync takes about a minute. Until it finishes, résumés are parsed but have no roles to match against.

In production, run `uv run fastapi run` for the API. Run `uv run jobs-ingest` as its own service with `--loop`, or from cron or a systemd timer.

## Job postings

`jobs-ingest` is the only process that writes `jobs.db`; the API opens it read-only. Each run works like this:

- **Skips recent runs.** It does nothing if the last successful run is more recent than `JOBS_REFRESH_HOURS` (6). `--force` overrides this.
- **Never overlaps.** A file lock next to the database stops two runs at once. That makes it safe to start from cron or a systemd timer as well as with `--loop`.
- **Saves bandwidth.** Requests are conditional (ETags), so unchanged boards cost almost nothing.
- **Updates in place.** Postings are upserted on (provider, board, posting id).
- **Closes jobs carefully.** A job missing from a board is only marked closed when that board's fetch was complete and not suspiciously empty.
- **Keeps the search index current.** An FTS5 index over titles, companies, locations and descriptions is maintained by triggers.

A cold sync takes about 80 seconds and downloads about 58 MB. A sync with no changes takes about a minute and about 3 MB, since every provider answers conditional requests. SmartRecruiters only returns descriptions from one request per posting, so each run fetches up to 50 per company. To fill them all at once, run `uv run jobs-ingest --force --sr-detail-cap 2000` (about 6 minutes).

To follow more employers, add their board identifiers to `jobs/boards.toml`. Look them up on each company's careers page.

To inspect `jobs.db`, use `uv run python` or a recent `sqlite3`. The `sqlite3` that ships with macOS (3.43) can't open it read-only after the writer has closed.

## How a résumé is processed

`POST /resumes` checks the PDF and answers `202` with an ID right away. A background task then does the rest, recording progress in `data/app.db`:

1. **Parse.** Gemini gets the PDF itself, which also works for scans, plus the link targets that `pypdf` finds in it.
   - It replies with RenderCV YAML, which RenderCV validates.
   - Up to three retries feed RenderCV's errors back to the model.
   - A typical résumé takes 5–15 seconds and costs about $0.01.
2. **Shortlist.** The best `MATCH_LIMIT` (1,000) open US/Canada roles are picked by BM25 against the candidate's titles and skills. Postings of one role in several locations count as one role.
3. **Score.** Jev answers five questions per role in one request: fit, interview chance, required qualifications, seniority, and whether the candidate can take the job without relocating.
   - Jev sees the professional content of the résumé only, never contact details. Years of experience are computed in code.
   - 1,000 roles take about 5 seconds and cost about $0.11.

## API

| Method | Path                            | Description                                                      |
| ------ | ------------------------------- | ---------------------------------------------------------------- |
| `POST` | `/resumes`                      | Upload a résumé PDF (form field `file`); returns its `id`        |
| `GET`  | `/resumes/{id}`                 | Status, progress, the parsed CV and its RenderCV YAML            |
| `GET`  | `/resumes/{id}/matches`         | Scored roles, best first (`limit`, default 100); fills in while scoring |
| `GET`  | `/resumes/{id}/rendercv.yaml`   | The parsed CV as a RenderCV YAML file                            |
| `GET`  | `/jobs`                         | Search open postings (`q`, `geo`, `limit`)                       |
| `GET`  | `/jobs/stats`                   | Open postings by provider and region, and the last sync          |
| `GET`  | `/health`                       | Liveness check: `{"status": "ok"}`                               |

## Checks

```sh
uv run pytest        # tests; Gemini, TypeSafe and the job boards are faked, so no keys or network needed
uv run ruff check    # lint
uv run ruff format   # format
uv run ty check      # type-check
```

`bun run check` at the repository root runs all of these after the frontend's checks.

## Layout

```
src/backend/
├ main.py            the FastAPI app
├ config.py          settings from the environment and .env
├ db.py              SQLite connections and the app.db schema
├ resumes/
│ ├ parse.py         PDF → RenderCV with Gemini
│ ├ pipeline.py      the background run: parse, shortlist, score
│ └ router.py        /resumes endpoints
├ matching/
│ ├ prefilter.py     FTS5 shortlist of roles
│ └ jev.py           Jev questions, request state and scores
└ jobs/
  ├ boards.toml      employers followed, by provider and board
  ├ himalayas.toml   Himalayas searches
  ├ providers.py     one fetcher per provider
  ├ normalize.py     provider payloads → one schema (+ text.py, geo.py)
  ├ store.py         the jobs.db schema and upserts
  ├ ingest.py        the jobs-ingest command
  └ router.py        /jobs endpoints
tests/               pytest tests
data/                jobs.db and app.db (gitignored)
```

This is uv's default packaged layout (`src/<package>/` with the `uv_build` backend). The package is installed into the environment, so tests import it as `backend` and `jobs-ingest` is on the path.
