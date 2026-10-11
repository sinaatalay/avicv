"""jobs.db: postings, their FTS5 index, per-board state, ETags and the ingest log.

Only `jobs-ingest` writes it, through `connect`; the API opens it read-only.

- `id INTEGER PRIMARY KEY` gives FTS5 (external content, joined on rowid) row ids that
  survive VACUUM; (provider, board, source_id) is UNIQUE and is the upsert target.
- A job is rewritten only when its `content_hash` changes. Unchanged jobs are not
  touched at all: `board_state.last_verified_at` records when a board was last fetched
  completely, which means every open job on it was still listed then.
- A job missing from its board is closed only after a complete fetch. If that fetch
  looks wrong (an empty board, or more than half of a 20+ job board gone at once),
  closing waits for the next complete fetch to agree.
- Search results (Himalayas) are never complete, so their jobs close when they expire
  or haven't been seen for a while instead.
"""

import hashlib
import json
import sqlite3
from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .normalize import COLUMNS, Record, unpack

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                 INTEGER PRIMARY KEY,
    provider           TEXT NOT NULL,     -- greenhouse | ashby | lever | smartrecruiters | himalayas
    board              TEXT NOT NULL,     -- board token or company id (Himalayas: company slug)
    source_id          TEXT NOT NULL,     -- the provider's posting id (Himalayas: guid URL)
    first_seen         TEXT NOT NULL,     -- our own UTC ISO 8601 timestamps
    last_seen          TEXT NOT NULL,     -- last written; Himalayas jobs also when seen again
    closed_at          TEXT,              -- no longer listed
    content_hash       TEXT NOT NULL,
    content_updated_at TEXT NOT NULL,     -- when content_hash last changed
    company            TEXT,
    title              TEXT NOT NULL,
    locations_text     TEXT,              -- raw location strings joined with ' | '
    locations_json     TEXT,              -- the same, as a JSON array
    countries          TEXT,              -- JSON array of ISO 3166-1 codes (heuristic)
    regions            TEXT,              -- JSON array such as ["US-CA", "CA-AB"] (heuristic)
    geo_bucket         TEXT,              -- us_ca | remote_unspecified | other | unknown
    workplace_type     TEXT,              -- remote | hybrid | onsite | NULL
    employment_type    TEXT,              -- full_time | part_time | contract | intern | temporary | other
    department         TEXT,
    team               TEXT,
    posted_at          TEXT,              -- as provided, see posted_at_field
    posted_at_field    TEXT,              -- first_published | publishedAt | createdAt | releasedDate | pubDate
    updated_at         TEXT,              -- as provided (Greenhouse, Lever)
    expires_at         TEXT,              -- as provided (Greenhouse deadline, Himalayas expiry)
    apply_url          TEXT,
    source_url         TEXT,
    salary_min         REAL,              -- only when the provider gives structured pay
    salary_max         REAL,
    salary_currency    TEXT,
    salary_interval    TEXT,              -- year | month | week | day | hour | NULL (not stated)
    salary_text        TEXT,              -- the provider's label, e.g. "Canada Annual Pay Range"
    description_text   TEXT,
    raw_zstd           BLOB NOT NULL,     -- zstd-compressed canonical JSON of the payload
    UNIQUE (provider, board, source_id)
);
-- Open jobs by geography, newest first (the API's listing), and by provider (stats).
CREATE INDEX IF NOT EXISTS jobs_open_geo ON jobs (closed_at, geo_bucket, first_seen);
CREATE INDEX IF NOT EXISTS jobs_open_provider ON jobs (closed_at, provider);

CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5 (
    title, company, locations_text, description_text,
    content = 'jobs', content_rowid = 'id',
    tokenize = 'porter unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS jobs_fts_insert AFTER INSERT ON jobs BEGIN
    INSERT INTO jobs_fts (rowid, title, company, locations_text, description_text)
    VALUES (new.id, new.title, new.company, new.locations_text, new.description_text);
END;
CREATE TRIGGER IF NOT EXISTS jobs_fts_delete AFTER DELETE ON jobs BEGIN
    INSERT INTO jobs_fts (jobs_fts, rowid, title, company, locations_text, description_text)
    VALUES ('delete', old.id, old.title, old.company, old.locations_text, old.description_text);
END;
CREATE TRIGGER IF NOT EXISTS jobs_fts_update
AFTER UPDATE OF title, company, locations_text, description_text ON jobs BEGIN
    INSERT INTO jobs_fts (jobs_fts, rowid, title, company, locations_text, description_text)
    VALUES ('delete', old.id, old.title, old.company, old.locations_text, old.description_text);
    INSERT INTO jobs_fts (rowid, title, company, locations_text, description_text)
    VALUES (new.id, new.title, new.company, new.locations_text, new.description_text);
END;

CREATE TABLE IF NOT EXISTS board_state (
    provider         TEXT NOT NULL,
    board            TEXT NOT NULL,
    last_attempt_at  TEXT NOT NULL,
    last_status      TEXT NOT NULL,       -- ok | not_modified | partial | suspect | failed
    last_verified_at TEXT,                -- last complete fetch: all open jobs were listed
    suspect_since    TEXT,                -- closures held back until a second fetch agrees
    suspect_missing  TEXT,                -- digest of the exact missing source IDs
    configured       INTEGER NOT NULL DEFAULT 1,
    open_jobs        INTEGER,
    PRIMARY KEY (provider, board)
);

-- ETags for conditional GETs. Saved only after the board's data was committed, and
-- only valid for the normalizer version they were saved with.
CREATE TABLE IF NOT EXISTS http_cache (
    url                TEXT PRIMARY KEY,
    etag               TEXT NOT NULL,
    normalizer_version TEXT NOT NULL,
    saved_at           TEXT NOT NULL,
    provider           TEXT,
    board              TEXT
);

CREATE TABLE IF NOT EXISTS ingest_runs (
    run_id         INTEGER PRIMARY KEY,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    status         TEXT NOT NULL,          -- running | ok | partial | failed | abandoned
    trigger        TEXT,                   -- cli | loop
    scope_key      TEXT,                   -- exact provider/configuration/normalizer scope
    boards_total   INTEGER,
    boards_ok      INTEGER,
    boards_partial INTEGER,
    boards_failed  INTEGER,
    jobs_seen      INTEGER,
    jobs_new       INTEGER,
    jobs_changed   INTEGER,
    jobs_unchanged INTEGER,
    jobs_closed    INTEGER,
    jobs_reopened  INTEGER,
    requests       INTEGER,
    retries        INTEGER,
    bytes_wire     INTEGER,
    bytes_decoded  INTEGER,
    detail_calls   INTEGER,
    wall_seconds   REAL,
    http_json      TEXT,                   -- per-host requests, statuses and bytes
    errors_json    TEXT
);

CREATE TABLE IF NOT EXISTS ingest_board_runs (
    run_id          INTEGER NOT NULL REFERENCES ingest_runs (run_id) ON DELETE CASCADE,
    provider        TEXT NOT NULL,
    board           TEXT NOT NULL,
    unit            TEXT NOT NULL DEFAULT '', -- e.g. a Himalayas search slice
    status          TEXT NOT NULL,          -- ok | not_modified | partial | suspect | failed
    complete        INTEGER NOT NULL,
    http_status     INTEGER,
    pages           INTEGER,
    requests        INTEGER,
    bytes_wire      INTEGER,
    reported_total  INTEGER,
    jobs_seen       INTEGER,
    jobs_new        INTEGER,
    jobs_changed    INTEGER,
    jobs_unchanged  INTEGER,
    jobs_closed     INTEGER,
    jobs_reopened   INTEGER,
    detail_calls    INTEGER,
    detail_reused   INTEGER,
    detail_deferred INTEGER,
    detail_seconds  REAL,
    duration_ms     INTEGER,
    error           TEXT,
    PRIMARY KEY (run_id, provider, board, unit)
);
"""

_UPDATABLE = [c for c in COLUMNS if c not in {"provider", "board", "source_id"}]
_UPSERT = f"""
INSERT INTO jobs ({", ".join(COLUMNS)}, first_seen, last_seen, content_updated_at)
VALUES ({", ".join(":" + c for c in COLUMNS)}, :now, :now, :now)
ON CONFLICT (provider, board, source_id) DO UPDATE SET
    {", ".join(f"{c} = excluded.{c}" for c in _UPDATABLE)},
    last_seen = excluded.last_seen,
    content_updated_at = excluded.last_seen,
    closed_at = NULL
WHERE jobs.content_hash IS NOT excluded.content_hash
"""
_BOARD_STATE = """
INSERT INTO board_state
    (provider, board, last_attempt_at, last_status, last_verified_at, suspect_since, suspect_missing, open_jobs)
VALUES (:provider, :board, :now, :status, :verified_at, :suspect_since, :suspect_missing, :open_jobs)
ON CONFLICT (provider, board) DO UPDATE SET
    last_attempt_at = excluded.last_attempt_at,
    last_status = excluded.last_status,
    last_verified_at = coalesce(excluded.last_verified_at, board_state.last_verified_at),
    suspect_since = excluded.suspect_since,
    suspect_missing = excluded.suspect_missing,
    configured = 1,
    open_jobs = excluded.open_jobs
"""
SUSPECT_MIN_OPEN = 20  # boards this big may not lose more than half their jobs at once


@dataclass
class Applied:
    """What one board's fetch did to the table."""

    status: str  # ok | partial | suspect
    seen: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    reopened: int = 0
    closed: int = 0
    skipped: int = 0


def connect(path: Path) -> sqlite3.Connection:
    """The writer's connection: autocommit with explicit transactions, WAL, schema applied.
    Usable from one worker thread at a time (normalizing runs off the event loop)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        path, timeout=30, isolation_level=None, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        init_schema(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version not in {0, 1, SCHEMA_VERSION}:
        msg = f"jobs.db has unsupported schema version {version}; use a compatible application version"
        raise RuntimeError(msg)
    if version == 1:
        with transaction(conn):
            conn.execute("ALTER TABLE board_state ADD COLUMN suspect_missing TEXT")
            conn.execute(
                "ALTER TABLE board_state ADD COLUMN configured INTEGER NOT NULL DEFAULT 1"
            )
            conn.execute("ALTER TABLE http_cache ADD COLUMN provider TEXT")
            conn.execute("ALTER TABLE http_cache ADD COLUMN board TEXT")
            conn.execute("ALTER TABLE ingest_runs ADD COLUMN scope_key TEXT")
            # Old ETags have no source ownership. Fetch fresh bodies once before caching again.
            conn.execute("DELETE FROM http_cache")
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


@contextmanager
def transaction(conn: sqlite3.Connection) -> Generator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def stored_raw(conn: sqlite3.Connection, provider: str, board: str) -> dict[str, Any]:
    """source_id -> stored payload (used to reuse SmartRecruiters detail responses)."""
    rows = conn.execute(
        "SELECT source_id, raw_zstd FROM jobs WHERE provider = ? AND board = ?",
        (provider, board),
    )
    return {row["source_id"]: unpack(row["raw_zstd"]) for row in rows}


def apply_board(
    conn: sqlite3.Connection,
    provider: str,
    board: str,
    items: Iterable[tuple[str, Any, str]],
    build_row: Callable[[Any, str], Record | None],
    now: str,
    *,
    complete: bool,
    track_seen: bool = False,
) -> Applied:
    """Upsert one board's fetched `(source_id, raw, content_hash)` items.

    `build_row` (HTML to text, geography: the expensive part) only runs for new or
    changed jobs. `track_seen` bumps `last_seen` on unchanged jobs too, for sources whose
    jobs expire by age instead of by board listings (Himalayas)."""
    existing = {
        row["source_id"]: (row["id"], row["content_hash"], row["closed_at"])
        for row in conn.execute(
            "SELECT id, source_id, content_hash, closed_at FROM jobs WHERE provider = ? AND board = ?",
            (provider, board),
        )
    }
    state = conn.execute(
        "SELECT suspect_since, suspect_missing FROM board_state WHERE provider = ? AND board = ?",
        (provider, board),
    ).fetchone()
    result = Applied(status="ok" if complete else "partial")
    fetched: set[str] = set()
    upserts: list[Record] = []
    reopen: list[int] = []
    touch: list[int] = []
    for source_id, raw, chash in items:
        if source_id in fetched:
            continue
        previous = existing.get(source_id)
        if previous is not None and previous[1] == chash:
            fetched.add(source_id)
            result.unchanged += 1
            if previous[2] is not None:
                reopen.append(previous[0])
            elif track_seen:
                touch.append(previous[0])
            continue
        row = build_row(raw, chash)
        if row is None:  # e.g. an unlisted posting
            result.skipped += 1
            continue
        fetched.add(source_id)
        if previous is None:
            result.new += 1
        else:
            result.changed += 1
            if previous[2] is not None:
                result.reopened += 1
        upserts.append(row | {"now": now})
    result.seen = len(fetched)
    result.reopened += len(reopen)

    open_before = {
        sid for sid, (_, _, closed_at) in existing.items() if closed_at is None
    }
    missing = [existing[sid][0] for sid in open_before - fetched]
    suspect_since = state["suspect_since"] if state else None
    suspect_missing = state["suspect_missing"] if state else None
    close = False
    if complete and missing:
        mass_drop = (
            len(open_before) >= SUSPECT_MIN_OPEN and len(missing) > len(open_before) / 2
        )
        signature = hashlib.sha256(
            json.dumps(sorted(open_before - fetched)).encode()
        ).hexdigest()
        if (not fetched or mass_drop) and signature != suspect_missing:
            result.status, suspect_since, suspect_missing = "suspect", now, signature
        else:
            close, suspect_since, suspect_missing = True, None, None
    elif complete:
        suspect_since, suspect_missing = None, None

    with transaction(conn):
        conn.executemany(_UPSERT, upserts)
        conn.executemany(
            "UPDATE jobs SET closed_at = NULL, last_seen = ? WHERE id = ?",
            [(now, i) for i in reopen],
        )
        conn.executemany(
            "UPDATE jobs SET last_seen = ? WHERE id = ?", [(now, i) for i in touch]
        )
        if close:
            conn.executemany(
                "UPDATE jobs SET closed_at = ? WHERE id = ?",
                [(now, i) for i in missing],
            )
            result.closed = len(missing)
        open_jobs = conn.execute(
            "SELECT count(*) FROM jobs WHERE provider = ? AND board = ? AND closed_at IS NULL",
            (provider, board),
        ).fetchone()[0]
        conn.execute(
            _BOARD_STATE,
            {
                "provider": provider,
                "board": board,
                "now": now,
                "status": result.status,
                "verified_at": now if complete and result.status == "ok" else None,
                "suspect_since": suspect_since,
                "suspect_missing": suspect_missing,
                "open_jobs": open_jobs,
            },
        )
    return result


def mark_not_modified(
    conn: sqlite3.Connection, provider: str, board: str, now: str
) -> int:
    """The board answered 304: its listing is what we stored. Returns its open jobs."""
    open_jobs = conn.execute(
        "SELECT count(*) FROM jobs WHERE provider = ? AND board = ? AND closed_at IS NULL",
        (provider, board),
    ).fetchone()[0]
    conn.execute(
        _BOARD_STATE,
        {
            "provider": provider,
            "board": board,
            "now": now,
            "status": "not_modified",
            "verified_at": now,
            "suspect_since": None,
            "suspect_missing": None,
            "open_jobs": open_jobs,
        },
    )
    return open_jobs


def mark_failed(conn: sqlite3.Connection, provider: str, board: str, now: str) -> None:
    conn.execute(
        "INSERT INTO board_state (provider, board, last_attempt_at, last_status) "
        "VALUES (?, ?, ?, 'failed') ON CONFLICT (provider, board) DO UPDATE SET "
        "last_attempt_at = excluded.last_attempt_at, last_status = 'failed'",
        (provider, board, now),
    )


def expire_unseen(
    conn: sqlite3.Connection,
    provider: str,
    now: str,
    unseen_since: str,
    *,
    allow_unseen: bool = True,
) -> int:
    """Close a search-sourced provider's jobs past their expiry or not seen recently."""
    return conn.execute(
        "UPDATE jobs SET closed_at = :now WHERE provider = :provider AND closed_at IS NULL "
        "AND ((:allow_unseen AND last_seen < :unseen_since) OR (expires_at IS NOT NULL AND expires_at < :now))",
        {
            "now": now,
            "provider": provider,
            "unseen_since": unseen_since,
            "allow_unseen": allow_unseen,
        },
    ).rowcount


def load_validators(
    conn: sqlite3.Connection, normalizer_version: str
) -> dict[str, str]:
    rows = conn.execute(
        "SELECT url, etag FROM http_cache WHERE normalizer_version = ?",
        (normalizer_version,),
    )
    return {row["url"]: row["etag"] for row in rows}


def save_validators(
    conn: sqlite3.Connection,
    validators: dict[str, str],
    normalizer_version: str,
    now: str,
    provider: str,
    board: str,
) -> None:
    """Replace a board's validator set, including when a new response has no ETag.

    Never retain an old first-page validator after a partial/multi-page transition.
    """
    with transaction(conn):
        conn.execute(
            "DELETE FROM http_cache WHERE provider = ? AND board = ?", (provider, board)
        )
        conn.executemany(
            "INSERT OR REPLACE INTO http_cache (url, etag, normalizer_version, saved_at, provider, board) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (url, etag, normalizer_version, now, provider, board)
                for url, etag in validators.items()
            ],
        )


def start_run(
    conn: sqlite3.Connection,
    started_at: str,
    trigger: str,
    scope_key: str | None = None,
) -> int:
    """Log a new run. Call while holding the run lock: any run still `running` is a
    leftover from a killed process."""
    conn.execute(
        "UPDATE ingest_runs SET status = 'abandoned', finished_at = ? WHERE status = 'running'",
        (started_at,),
    )
    cursor = conn.execute(
        "INSERT INTO ingest_runs (started_at, status, trigger, scope_key) VALUES (?, 'running', ?, ?)",
        (started_at, trigger, scope_key),
    )
    return cursor.lastrowid or 0


def log_board(conn: sqlite3.Connection, run_id: int, row: dict[str, Any]) -> None:
    columns = ["run_id", *row]
    conn.execute(
        f"INSERT OR REPLACE INTO ingest_board_runs ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        [run_id, *row.values()],
    )


def finish_run(conn: sqlite3.Connection, run_id: int, fields: dict[str, Any]) -> None:
    assignments = ", ".join(f"{name} = ?" for name in fields)
    conn.execute(
        f"UPDATE ingest_runs SET {assignments} WHERE run_id = ?",
        [*fields.values(), run_id],
    )


def fail_running(conn: sqlite3.Connection, now: str) -> None:
    conn.execute(
        "UPDATE ingest_runs SET status = 'failed', finished_at = ? WHERE status = 'running'",
        (now,),
    )


def last_success_started_at(
    conn: sqlite3.Connection, scope_key: str | None = None
) -> str | None:
    """Only the latest run, with exactly this scope, may suppress another attempt.

    A partial or failed retry invalidates an earlier success; legacy runs with unknown
    scope cannot suppress a configured run.
    """
    row = conn.execute(
        "SELECT started_at, status, finished_at, scope_key FROM ingest_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    return (
        row["started_at"]
        if row
        and row["status"] == "ok"
        and row["finished_at"]
        and row["scope_key"] == scope_key
        else None
    )


def reconcile_boards(conn: sqlite3.Connection, boards: list[dict[str, Any]]) -> None:
    """Removed ATS boards remain available but are explicitly no longer verified.

    Reconcile against the full configured inventory, even on a --only run. Search
    sources use per-job last_seen/expiry instead of an exhaustive board inventory.
    """
    active = {(b["provider"], b["id"]) for b in boards}
    for row in conn.execute(
        "SELECT provider, board FROM board_state WHERE provider != 'himalayas'"
    ).fetchall():
        conn.execute(
            "UPDATE board_state SET configured = ? WHERE provider = ? AND board = ?",
            (
                int((row["provider"], row["board"]) in active),
                row["provider"],
                row["board"],
            ),
        )


def source_health(
    conn: sqlite3.Connection, stale_after_hours: float = 24.0
) -> dict[tuple[str, str], dict[str, Any]]:
    """Conservative board freshness without closing jobs on unavailable sources.

    Works with the previous schema while the independent ingester has not upgraded
    it yet. ATS verification is board-wide; search jobs are verified individually.
    """
    cutoff = (datetime.now(UTC) - timedelta(hours=stale_after_hours)).isoformat()
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in conn.execute("SELECT * FROM board_state"):
        state = dict(row)
        provider, board = state["provider"], state["board"]
        configured = bool(state.get("configured", 1))
        verified = state["last_verified_at"]
        status = state["last_status"] if configured else "unconfigured"
        out[(provider, board)] = {
            "provider": provider,
            "board": board,
            "status": status,
            "configured": configured,
            "last_attempt_at": state["last_attempt_at"],
            "last_verified_at": verified,
            "stale": status not in {"ok", "not_modified"}
            or not verified
            or verified < cutoff,
        }
    # A successful bounded search is not a complete board inventory. Its job-level
    # last_seen is exposed separately; the latest search errors still mark the source.
    latest_search = conn.execute(
        "SELECT status FROM ingest_board_runs WHERE provider = 'himalayas' "
        "AND run_id = (SELECT max(run_id) FROM ingest_board_runs WHERE provider = 'himalayas')"
    ).fetchall()
    search_statuses = {r["status"] for r in latest_search}
    search_status = (
        "ok"
        if search_statuses == {"ok"}
        else "failed"
        if search_statuses == {"failed"}
        else "partial"
    )
    for row in conn.execute(
        "SELECT board, max(last_seen) AS last_seen FROM jobs WHERE provider = 'himalayas' AND closed_at IS NULL GROUP BY board"
    ):
        out[("himalayas", row["board"])] = {
            "provider": "himalayas",
            "board": row["board"],
            "status": search_status,
            "configured": True,
            "last_attempt_at": None,
            "last_verified_at": row["last_seen"],
            "stale": search_status != "ok" or row["last_seen"] < cutoff,
        }
    return out


def prune_logs(conn: sqlite3.Connection, before: str) -> None:
    """Drop per-board log rows of runs started before `before` (run totals stay)."""
    conn.execute(
        "DELETE FROM ingest_board_runs WHERE run_id IN "
        "(SELECT run_id FROM ingest_runs WHERE started_at < ?)",
        (before,),
    )
