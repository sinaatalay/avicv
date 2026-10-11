"""SQLite access.

There are two database files in the data directory:

- `jobs.db` holds job postings. Only the `jobs-ingest` command writes it; the API opens it read-only.
- `app.db` holds uploaded résumés and their match results, and is written by the API.

Keeping them apart means a long ingest transaction never blocks an upload, and the jobs data can be
rebuilt without touching users' results.
"""

import sqlite3
from pathlib import Path

APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS resumes (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    filename        TEXT,
    pdf_sha256      TEXT NOT NULL,
    status          TEXT NOT NULL,              -- parsing | matching | done | failed
    error           TEXT,
    parse_preview   TEXT,                       -- JSON: name and section titles seen while parsing
    rendercv_yaml   TEXT,                       -- the parsed CV, as RenderCV YAML
    parse_seconds   REAL,
    parse_cost_usd  REAL,
    match_total     INTEGER NOT NULL DEFAULT 0, -- jobs shortlisted for scoring
    match_done      INTEGER NOT NULL DEFAULT 0, -- jobs scored so far
    match_cost_usd  REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS matches (
    resume_id       TEXT NOT NULL REFERENCES resumes (id) ON DELETE CASCADE,
    job_id          INTEGER NOT NULL,           -- jobs.db: jobs.id
    job_hash        TEXT NOT NULL,              -- jobs.db: jobs.content_hash when it was scored
    questions       TEXT NOT NULL,              -- version of the Jev question set
    score           REAL NOT NULL,              -- composite, 0 to 1
    fit             REAL NOT NULL,              -- 0 to 4
    interview       REAL NOT NULL,              -- probabilities, 0 to 1
    must_haves      REAL NOT NULL,
    location        REAL NOT NULL,
    seniority       TEXT NOT NULL,              -- below | matches | above
    locations       TEXT NOT NULL,              -- JSON array: every location of the role's postings
    input_tokens    INTEGER,
    scored_at       TEXT NOT NULL,
    PRIMARY KEY (resume_id, job_id, questions)
);
CREATE INDEX IF NOT EXISTS matches_by_score ON matches (resume_id, score DESC);
"""


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Open a connection with rows as `sqlite3.Row`. Read-only connections fail if the file is missing."""
    if readonly:
        # Not mode=ro: a reader of a WAL database must be able to recreate the -shm and -wal files,
        # which SQLite deletes when the last connection closes. query_only blocks every write instead.
        conn = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True)
        conn.execute("PRAGMA query_only = ON")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


# Columns added after a table was first created: (table, column, declaration)
ADDED_COLUMNS = [("resumes", "parse_preview", "TEXT")]


def connect_app(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    conn.executescript(APP_SCHEMA)
    for table, column, declaration in ADDED_COLUMNS:
        if column not in {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
        }:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
    return conn
