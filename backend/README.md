# backend

The FastAPI app behind AviCV, on Python 3.14 and managed with [uv](https://docs.astral.sh/uv/). It is a member of the uv workspace rooted at the repository root, so `uv.lock`, `.python-version` and `.venv` live there.

It does four things:

1. **Collects job postings.** `jobs-ingest` pulls the public job boards of the employers listed in `jobs/boards.toml` (Greenhouse, Ashby, Lever and SmartRecruiters), plus remote-job searches on Himalayas, into `data/jobs.db`. It refreshes them every 6 hours.
2. **Parses résumés.** Gemini 3.8 Flash, called through OpenRouter with [Pydantic AI](https://ai.pydantic.dev), reads an uploaded PDF and writes it as [RenderCV](https://docs.rendercv.com) YAML. RenderCV validates the YAML. If validation fails, RenderCV's error messages go back to the model so it can correct them.
3. **Matches résumés to jobs.** Full-text search shortlists open US/Canada roles. Then [Jev](https://docs.typesafe.ai), TypeSafe's decision model, scores each role against the résumé.
4. **Creates tailored PDFs.** A background vision loop reconstructs the uploaded CV's design once. Each job-specific CV selects relevant source evidence and renders with those saved settings.

## Setup

```sh
cp .env.example .env   # then add OPENROUTER_API_KEY and TYPESAFE_API_KEY
uv sync                # from anywhere in the repo
```

`.env.example` lists the optional settings and their defaults. You can also set any of them as an environment variable.

### OpenRouter BYOK

Parsing, design reconstruction, and tailoring route through `OPENROUTER_PROVIDER`
(default `google-vertex`), with cross-provider fallback disabled. The OpenRouter
API key in `backend/.env` must belong to the workspace containing the prioritized
Vertex credential and match its **API Keys**, **Models**, and **Members** filters.
The Google service account stays in OpenRouter; do not put it in this app's env file.

Provider selection alone does not enforce BYOK. In that workspace's Vertex BYOK
settings, choose **Never use OpenRouter's Google Vertex capacity for any model**
to prevent shared-capacity billing. See [OpenRouter's BYOK documentation](https://openrouter.ai/docs/guides/overview/auth/byok).

Run `uv run python -m backend.check_openrouter` from `backend/` to verify the
actual route. It makes one tiny, billable request per distinct configured model,
using the same factory as the app and no CV data. It prints the provider,
generation ID, and OpenRouter's `is_byok` flag; exits successfully only when every
model explicitly reports `is_byok: true`. A missing flag or `false` fails the
check. Inspect that generation in OpenRouter Activity for provider attempts;
no BYOK attempt points to workspace/filter eligibility, while failed BYOK
attempts show upstream credential, permission, or quota errors.

## Developing

```sh
uv run fastapi dev          # http://127.0.0.1:8000, docs at /docs
uv run jobs-ingest --loop   # fills data/jobs.db, then keeps it fresh
```

`bun run dev` at the repository root starts both, along with the frontend. Until the first usable job sync finishes, parsed résumés wait in the durable queue and automatically continue when jobs become available.

In production, run `uv run fastapi run` for the API. Run `uv run jobs-ingest` as its own service with `--loop`, or from cron or a systemd timer.

## Job postings

`jobs-ingest` is the only process that writes `jobs.db`; the API opens it read-only. Each run works like this:

- **Skips recent complete runs.** It does nothing if the last successful run is more recent than `JOBS_REFRESH_HOURS` (6). Failures, deferred details, partial results, and suspicious deletions remain eligible for retry. `--force` overrides freshness.
- **Never overlaps.** A file lock next to the database stops two runs at once. That makes it safe to start from cron or a systemd timer as well as with `--loop`.
- **Saves bandwidth.** Requests are conditional (ETags), so unchanged boards cost almost nothing.
- **Updates in place.** Postings are upserted on (provider, board, posting id).
- **Closes jobs carefully.** A job missing from a board is only marked closed when that board's fetch was complete and not suspiciously empty. Large removals require a repeated, identical set of missing IDs.
- **Tracks source health.** Attempt time and successful verification time are separate. Scoped `--only` runs cannot mark the entire catalog healthy. SmartRecruiters detail freshness is tied to the corresponding listing revision.
- **Keeps the search index current.** An FTS5 index over titles, companies, locations and descriptions is maintained by triggers.

A cold sync takes about 80 seconds and downloads about 58 MB. A sync with no changes takes about a minute and about 3 MB, since every provider answers conditional requests. SmartRecruiters only returns descriptions from one request per posting, so each run fetches up to 50 per company. To fill them all at once, run `uv run jobs-ingest --force --sr-detail-cap 2000` (about 6 minutes).

To follow more employers, add their board identifiers to `jobs/boards.toml`. Look them up on each company's careers page.

To inspect `jobs.db`, use `uv run python` or a recent `sqlite3`. The `sqlite3` that ships with macOS (3.43) can't open it read-only after the writer has closed.

## How a résumé is processed

`POST /resumes` checks the PDF and answers `202` only after the upload and queue entry are committed to `data/app.db`. A worker claims one résumé at a time across all API processes sharing that database. Renewable leases, fenced writes, and saved per-role outcomes let processing recover after restarts without losing completed scores. The queue has a configurable capacity and attempt timeout.

1. **Parse.** Gemini gets the PDF itself, which also works for scans, plus the link targets that `pypdf` finds in it.
   - It replies with RenderCV YAML, which RenderCV validates.
   - Up to three retries feed RenderCV's errors back to the model.
   - A typical résumé takes 5–15 seconds and costs about $0.01.
2. **Shortlist.** Full-text search ranks open US/Canada roles and remote roles with unspecified geography against titles, skills, education, publications, and project/experience highlights. Up to `MATCH_LIMIT` (1,000) distinct roles are scored. Equivalent postings in multiple locations count as one role; different descriptions or work arrangements remain separate. Diagnostics record catalog size, eligible postings, keyword matches, distinct roles, and the shortlist size.
3. **Score.** Jev answers five questions per role in one request: fit, interview fit, required qualifications, seniority, and location compatibility. These are model estimates, not calibrated hiring probabilities.
   - Jev sees the professional content of the résumé only, never contact details. Years of experience are computed in code.
   - The CV location is used when present; otherwise the candidate is assumed to be in the United States. A country-only location checks country eligibility without inventing a city or commute.
   - At most `MATCH_CONCURRENCY` (32) requests run together. Attempts, successes, failures, and pending work are counted separately. Retrying a partial run requests only its failed roles. Cost is an estimate; provider latency and charges vary.

Scores retain immutable job snapshots and stable provider identities, so a rebuilt catalog cannot attach an old score to a different job with a reused numeric ID. Matching fingerprints include candidate context, model, question version, and the actual job scoring input. Rematches preserve previous results until the replacement produces results. Legacy scores whose original job cannot be verified are displayed with unavailable details rather than guessed metadata.

## Tailored CVs and original design

After parsing is saved, the API enqueues a separate design task and continues matching immediately. The design worker renders the original PDF into page images, measures its font sizes and font families when available, asks the vision model for RenderCV settings, renders a draft, compares every original and draft page, and refines the settings within `DESIGN_MAX_ITERATIONS`. It saves the best usable design once per uploaded CV. Font substitution and layout differences are possible; the visual match is an approximation. All tailored versions reuse those exact saved settings. Historical uploads without their original PDF use a clearly identified default design.

`Tailor CV` selects and orders evidence from the parsed CV against the saved job description. The model returns source-entry and highlight references rather than new claims. Code assembles the document from the original values, preserving employment and education entries, chronology, identity, dates, qualifications, and metrics. Optional sections and highlights can be selected for relevance. It then renders a new PDF with the saved design.

Design and tailoring each have an independent leased worker lane, separate from matching. Requests, checkpoints, chosen designs, tailored YAML, and completed PDFs persist in `app.db`. Repeated requests reuse the same result when the CV, job snapshot, design, model, and renderer versions agree. Interrupted work resumes from saved checkpoints; deleting a CV also removes its generated artifacts. There is no dependency on an open browser tab.

Rendering uses the pinned RenderCV 2.8 API, bundled fonts, Typst, and portable PDFium previews installed by `uv sync`. Its fixed Typst icon dependency is vendored with its license and verified digest, so PDF compilation works offline. Each render runs in a disposable subprocess with a restricted environment, filesystem root, execution deadline, and output limits. Custom filesystem themes, photos, executable document markup, and arbitrary external assets are excluded from this upload-to-PDF path. The model still has access to RenderCV's built-in design settings and entry templates.

The frontend saves recent result links, filters, pagination preferences, and bounded job previews in localStorage. Full PDFs, parsed CVs, durable progress, and complete scores remain in SQLite. Back up both `app.db` and `jobs.db` using SQLite's backup API; copy neither live database without its WAL. This remains a trusted/local application: a résumé ID grants access, so public multi-user deployment requires authentication and ownership controls.

## API

| Method | Path                            | Description                                                      |
| ------ | ------------------------------- | ---------------------------------------------------------------- |
| `POST` | `/resumes`                      | Upload a résumé PDF (form field `file`); returns its `id`        |
| `GET`  | `/resumes/{id}`                 | Status, progress, the parsed CV and its RenderCV YAML            |
| `GET`  | `/resumes/{id}/matches`         | Scored roles, best first (`limit`, default 100); fills in while scoring |
| `GET`  | `/resumes/{id}/match-results`   | Counts and results; `category=all\|good\|other`, `nearby`/`include_closed`, `limit` ≤ 500, `offset` |
| `GET`  | `/resumes/{id}/good-match-links` | All unique available application URLs for good matches, ranked and unpaginated |
| `GET`  | `/resumes/{id}/time-savings`     | Unique completed work and explicit manual-effort estimates for this CV |
| `POST` | `/resumes/{id}/retry`           | Resume failed processing or retry failed role scores             |
| `POST` | `/resumes/{id}/rematch`         | Refresh against the catalog; optional JSON `location` override   |
| `DELETE` | `/resumes/{id}`               | Delete uploaded PDF, parsed CV, queue entry, and saved scores     |
| `POST` | `/resumes/{id}/tailor`          | Request/reuse a tailored CV with JSON `job_key`                   |
| `GET` | `/resumes/{id}/tailored/{artifact_id}` | Preparation status and download availability                |
| `GET` | `/resumes/{id}/tailored/{artifact_id}/cv.pdf` | Download the completed tailored PDF                |
| `GET` | `/resumes/{id}/design`          | Background design status and source                            |
| `GET`  | `/resumes/{id}/rendercv.yaml`   | The parsed CV as a RenderCV YAML file                            |
| `GET`  | `/jobs`                         | Search open postings (`q`, `geo`, `limit`)                       |
| `GET`  | `/jobs/stats`                   | Open postings by provider and region, and the last sync          |
| `GET`  | `/health`                       | Liveness check: `{"status": "ok"}`                               |

Good matches have a composite score strictly above `0.80`; other jobs have a score at or below it. `good_total` and `other_total` describe the full available, location-filtered set before category pagination. Jobs within each category remain ordered by descending match score. The UI uses the categories and application links without exposing score percentages or processing diagnostics.

Time saved is an estimate of equivalent manual effort: 30 seconds per successfully reviewed job and 10 minutes per completed tailored CV. These are product assumptions, not measured user time or research-backed benchmarks. Counts come from persisted results across matching generations and ready PDF artifacts, scoped to one uploaded CV. Stable posting identities and explicit groups prevent revisions, retries, rematches and repeat downloads from adding credit for the same job. Legacy records with no verifiable identity or saved URL are omitted. Background runtime is not subtracted because it does not measure the user's effort.

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
│ ├ pipeline.py      resumable parse, shortlist, and score stages
│ ├ queue.py         transactional claims, leases, and recovery
│ ├ storage.py       snapshots, score identities, and progress
│ ├ design.py        original/draft image comparison and reusable design selection
│ ├ tailor.py        validated selection of original CV evidence
│ ├ render.py        isolated RenderCV PDF rendering and page previews
│ ├ artifact_store.py durable design requests and tailored PDF cache
│ ├ artifact_queue.py independent design/tailoring claims, leases, and recovery
│ ├ artifacts.py     checkpointed artifact processing
│ ├ artifact_router.py tailoring, status, and PDF download endpoints
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
