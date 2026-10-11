"""SQLite storage and additive, transactional application migrations.

Job snapshots belong to app.db: rebuilding the independently ingested jobs.db must
never change the job to which a saved score refers.
"""

import sqlite3
from pathlib import Path

RESUMES_SCHEMA = """
CREATE TABLE IF NOT EXISTS resumes (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, filename TEXT, pdf_sha256 TEXT NOT NULL,
    status TEXT NOT NULL, error TEXT, parse_preview TEXT, rendercv_yaml TEXT,
    parse_seconds REAL, parse_cost_usd REAL,
    match_total INTEGER NOT NULL DEFAULT 0, match_done INTEGER NOT NULL DEFAULT 0,
    match_cost_usd REAL NOT NULL DEFAULT 0
)
"""
MATCHES_SCHEMA = """
CREATE TABLE matches (
    resume_id TEXT NOT NULL REFERENCES resumes (id) ON DELETE CASCADE,
    job_key TEXT NOT NULL, candidate_revision INTEGER NOT NULL DEFAULT 0,
    generation INTEGER NOT NULL DEFAULT 0,
    job_id INTEGER NOT NULL, job_hash TEXT NOT NULL, questions TEXT NOT NULL,
    job_snapshot TEXT, provider TEXT, board TEXT, source_id TEXT,
    score REAL NOT NULL, fit REAL NOT NULL, interview REAL NOT NULL, must_haves REAL NOT NULL,
    location REAL, seniority TEXT NOT NULL, locations TEXT NOT NULL,
    input_tokens INTEGER, scored_at TEXT NOT NULL,
    PRIMARY KEY (resume_id, candidate_revision, job_key, questions)
)
"""
WORK_SCHEMA = """
CREATE TABLE IF NOT EXISTS resume_work (
    resume_id TEXT NOT NULL REFERENCES resumes (id) ON DELETE CASCADE,
    job_key TEXT NOT NULL, candidate_revision INTEGER NOT NULL, questions TEXT NOT NULL,
    generation INTEGER NOT NULL, job_snapshot TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'succeeded', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
    PRIMARY KEY (resume_id, candidate_revision, job_key, questions)
)
"""
QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS resume_queue (
    resume_id TEXT PRIMARY KEY REFERENCES resumes (id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK (state IN ('pending', 'running', 'done')),
    available_at REAL NOT NULL, owner TEXT, lease_until REAL,
    attempts INTEGER NOT NULL DEFAULT 0
)
"""
DESIGN_SCHEMA = """
CREATE TABLE IF NOT EXISTS resume_designs (
    id TEXT PRIMARY KEY,
    resume_id TEXT NOT NULL UNIQUE REFERENCES resumes (id) ON DELETE CASCADE,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    version TEXT NOT NULL, model TEXT NOT NULL, source_yaml TEXT NOT NULL,
    source_pdf_hash TEXT NOT NULL, status TEXT NOT NULL,
    design_json TEXT, source TEXT, quality_score REAL, iterations INTEGER,
    notes TEXT, error TEXT, cost_usd REAL NOT NULL DEFAULT 0
)
"""
TAILORED_SCHEMA = """
CREATE TABLE IF NOT EXISTS tailored_cvs (
    id TEXT PRIMARY KEY,
    resume_id TEXT NOT NULL REFERENCES resumes (id) ON DELETE CASCADE,
    design_id TEXT NOT NULL REFERENCES resume_designs (id) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL, job_key TEXT NOT NULL, source_yaml TEXT NOT NULL,
    job_snapshot TEXT NOT NULL, model TEXT NOT NULL, version TEXT NOT NULL,
    render_version TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    status TEXT NOT NULL, error TEXT, plan_json TEXT, tailored_yaml TEXT,
    pdf_bytes BLOB, filename TEXT, cost_usd REAL NOT NULL DEFAULT 0,
    UNIQUE (resume_id, fingerprint)
)
"""
ARTIFACT_QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifact_queue (
    kind TEXT NOT NULL CHECK (kind IN ('design', 'tailor')),
    target_id TEXT NOT NULL,
    resume_id TEXT NOT NULL REFERENCES resumes (id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK (state IN ('pending', 'running', 'done')),
    available_at REAL NOT NULL, owner TEXT, lease_until REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (kind, target_id)
)
"""
ADDED_COLUMNS = [
    ("parse_preview", "TEXT"),
    ("upload_pdf", "BLOB"),
    ("match_attempted", "INTEGER NOT NULL DEFAULT 0"),
    ("match_failed", "INTEGER NOT NULL DEFAULT 0"),
    ("search_stats", "TEXT"),
    ("results_context", "TEXT"),
    ("candidate_revision", "INTEGER NOT NULL DEFAULT 0"),
    ("work_generation", "INTEGER NOT NULL DEFAULT 1"),
    ("planned_generation", "INTEGER NOT NULL DEFAULT -1"),
    ("current_generation", "INTEGER NOT NULL DEFAULT 0"),
]
SCHEMA_VERSION = 3


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    path = path.expanduser().resolve()
    if readonly:
        # mode=rw recreates a missing WAL shared-memory file on older SQLite builds;
        # query_only prohibits SQL writes on this connection.
        conn = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True)
        conn.execute("PRAGMA query_only = ON")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, uri=True)
        conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def connect_app(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(f"Unsupported app.db schema version {version}")
    if version == SCHEMA_VERSION:
        return conn
    try:
        # Serialize migration with concurrent API startup; recheck after obtaining the lock.
        conn.execute("BEGIN IMMEDIATE")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            conn.commit()
            return conn
        conn.execute(RESUMES_SCHEMA)
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(resumes)")}
        for name, declaration in ADDED_COLUMNS:
            if name not in columns:
                conn.execute(f"ALTER TABLE resumes ADD COLUMN {name} {declaration}")
        match_columns = {r["name"] for r in conn.execute("PRAGMA table_info(matches)")}
        if match_columns and "job_key" not in match_columns:
            conn.execute("ALTER TABLE matches RENAME TO matches_legacy")
            conn.execute(MATCHES_SCHEMA)
            conn.execute("""
                INSERT INTO matches (resume_id, job_key, job_id, job_hash, questions,
                    score, fit, interview, must_haves, location, seniority, locations,
                    input_tokens, scored_at)
                SELECT resume_id, 'legacy:' || job_id || ':' || job_hash, job_id, job_hash, questions,
                    score, fit, interview, must_haves, location, seniority, locations,
                    input_tokens, scored_at FROM matches_legacy
            """)
            conn.execute("DROP TABLE matches_legacy")
        elif not match_columns:
            conn.execute(MATCHES_SCHEMA)
        # Early schema v1 installations required location even when unknown.
        match_info = list(conn.execute("PRAGMA table_info(matches)"))
        if any(
            column["name"] == "location" and column["notnull"] for column in match_info
        ):
            conn.execute("ALTER TABLE matches RENAME TO matches_v1")
            conn.execute(MATCHES_SCHEMA)
            names = ", ".join(column["name"] for column in match_info)
            conn.execute(
                f"INSERT INTO matches ({names}) SELECT {names} FROM matches_v1"
            )
            conn.execute("DROP TABLE matches_v1")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS matches_by_score ON matches (resume_id, generation, score DESC)"
        )
        conn.execute(WORK_SCHEMA)
        conn.execute(QUEUE_SCHEMA)
        conn.execute(DESIGN_SCHEMA)
        conn.execute(TAILORED_SCHEMA)
        conn.execute(ARTIFACT_QUEUE_SCHEMA)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS artifact_queue_ready ON artifact_queue (kind, state, available_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS queue_ready ON resume_queue (state, available_at)"
        )
        if version < 1:
            # Old match_done counted failed attempts. Preserve that as attempted while
            # deriving successful and failed counts from actual saved results.
            conn.execute("""
                UPDATE resumes SET match_attempted = MAX(match_done, (
                    SELECT COUNT(*) FROM matches WHERE resume_id = resumes.id)),
                    match_done = (SELECT COUNT(*) FROM matches WHERE resume_id = resumes.id)
            """)
            conn.execute(
                "UPDATE resumes SET match_failed = MAX(0, match_attempted - match_done)"
            )
            conn.execute(
                "UPDATE resumes SET status = 'partial' WHERE status = 'done' AND match_failed > 0"
            )
        # An unplanned run must not share the legacy visible generation.
        conn.execute("""
            UPDATE resumes SET work_generation = current_generation + 1
            WHERE planned_generation = -1 AND work_generation <= current_generation
        """)
        conn.execute("""
            UPDATE resumes SET results_context = search_stats
            WHERE results_context IS NULL AND current_generation = planned_generation
        """)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    except BaseException:
        conn.rollback()
        conn.close()
        raise
    return conn
