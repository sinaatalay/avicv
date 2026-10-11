import sqlite3
from pathlib import Path

import pytest

from backend.db import connect


def test_readonly_connection_to_a_closed_wal_database(tmp_path: Path) -> None:
    path = tmp_path / "jobs.db"
    writer = connect(path)
    writer.execute("CREATE TABLE jobs (title TEXT)")
    writer.execute("INSERT INTO jobs VALUES ('Backend Engineer')")
    writer.commit()
    writer.close()
    # SQLite removes the WAL files once the last connection closes
    assert not (tmp_path / "jobs.db-shm").exists()

    reader = connect(path, readonly=True)
    assert (
        reader.execute("SELECT title FROM jobs").fetchone()["title"]
        == "Backend Engineer"
    )
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        reader.execute("DELETE FROM jobs")
    reader.close()


def test_readonly_connection_needs_an_existing_file(tmp_path: Path) -> None:
    with pytest.raises(sqlite3.OperationalError):
        connect(tmp_path / "missing.db", readonly=True)
    assert not (tmp_path / "missing.db").exists()


def test_relative_database_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    writer = connect(Path("relative/jobs.db"))
    writer.execute("CREATE TABLE example (id INTEGER)")
    writer.close()
    reader = connect(Path("relative/jobs.db"), readonly=True)
    assert reader.execute("SELECT count(*) FROM example").fetchone()[0] == 0
    reader.close()


def test_settings_bounds_and_consistent_relative_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from pydantic import ValidationError

    from backend.config import BACKEND_DIR, Settings

    monkeypatch.chdir(tmp_path)
    assert Settings(data_dir="data", _env_file=None).data_dir == BACKEND_DIR / "data"
    for field, value in [
        ("match_concurrency", 0),
        ("match_limit", -1),
        ("queue_lease_seconds", 0),
        ("jobs_refresh_hours", 0),
    ]:
        with pytest.raises(ValidationError):
            Settings.model_validate({field: value})


def legacy_database(path: Path) -> None:
    conn = connect(path)
    conn.executescript("""
        CREATE TABLE resumes (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, filename TEXT, pdf_sha256 TEXT NOT NULL,
            status TEXT NOT NULL, error TEXT, rendercv_yaml TEXT, parse_seconds REAL,
            parse_cost_usd REAL, match_total INTEGER NOT NULL DEFAULT 0,
            match_done INTEGER NOT NULL DEFAULT 0, match_cost_usd REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE matches (
            resume_id TEXT NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
            job_id INTEGER NOT NULL, job_hash TEXT NOT NULL, questions TEXT NOT NULL,
            score REAL NOT NULL, fit REAL NOT NULL, interview REAL NOT NULL,
            must_haves REAL NOT NULL, location REAL NOT NULL, seniority TEXT NOT NULL,
            locations TEXT NOT NULL, input_tokens INTEGER, scored_at TEXT NOT NULL,
            PRIMARY KEY (resume_id, job_id, questions)
        );
        INSERT INTO resumes (id, created_at, pdf_sha256, status, match_total, match_done)
            VALUES ('legacy', '2026-10-11', 'pdfhash', 'done', 3, 3);
        INSERT INTO matches VALUES ('legacy', 1, 'hash1', '1', .9, 3.8, .8, .9, .1, 'matches', '[]', 100, '2026-10-11');
        INSERT INTO matches VALUES ('legacy', 2, 'different-hash', '1', .8, 3.5, .7, .8, .1, 'matches', '[]', 90, '2026-10-11');
    """)
    conn.commit()
    conn.close()


def test_migration_preserves_scores_and_backfills_only_verified_identity(
    settings, jobs_db: Path
) -> None:
    import json

    from backend.db import connect_app
    from backend.resumes.storage import backfill_snapshots

    legacy_database(settings.app_db)
    # Real catalog rows include compressed provider bytes; minimal fixtures once
    # hid a startup serialization failure during historical backfill.
    catalog = connect(jobs_db)
    catalog.execute("ALTER TABLE jobs ADD COLUMN raw_zstd BLOB")
    catalog.execute(
        "UPDATE jobs SET raw_zstd = ?", (b"\x28\xb5\x2f\xfdprovider-payload",)
    )
    catalog.commit()
    catalog.close()
    conn = connect_app(settings.app_db)
    row = conn.execute("SELECT * FROM resumes").fetchone()
    assert row["status"] == "partial"
    assert (row["match_attempted"], row["match_done"], row["match_failed"]) == (3, 2, 1)
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 2
    conn.close()
    backfill_snapshots(settings)
    # Idempotence is required across worker restarts.
    connect_app(settings.app_db).close()
    backfill_snapshots(settings)
    conn = connect_app(settings.app_db)
    rows = conn.execute("SELECT * FROM matches ORDER BY job_id").fetchall()
    assert len(rows) == 2
    assert rows[0]["score"] == 0.9
    assert json.loads(rows[0]["job_snapshot"])["title"] == "Backend Engineer, Payments"
    assert rows[0]["provider"] == "greenhouse"
    assert "raw_zstd" not in json.loads(rows[0]["job_snapshot"])
    assert rows[1]["job_snapshot"] is None
    assert rows[1]["provider"] is None
    assert rows[1]["score"] == 0.8
    conn.close()


def test_v1_upgrade_keeps_progress_and_separates_unplanned_generation(
    tmp_path: Path,
) -> None:
    from backend.db import MATCHES_SCHEMA, connect_app

    path = tmp_path / "app.db"
    conn = connect_app(path)
    conn.execute("DROP TABLE matches")
    conn.execute(MATCHES_SCHEMA.replace("location REAL,", "location REAL NOT NULL,"))
    conn.execute("ALTER TABLE resumes DROP COLUMN results_context")
    conn.execute("PRAGMA user_version = 1")
    conn.execute(
        "INSERT INTO resumes (id, created_at, pdf_sha256, status, work_generation, match_total, match_done, match_attempted) VALUES ('old', 'now', 'hash', 'done', 0, 2, 2, 2)"
    )
    conn.commit()
    conn.close()
    upgraded = connect_app(path)
    row = upgraded.execute("SELECT * FROM resumes").fetchone()
    assert row["work_generation"] == 1
    assert row["current_generation"] == 0
    assert row["match_done"] == row["match_attempted"] == 2
    assert "results_context" in dict(row)
    assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 2
    assert (
        next(
            column
            for column in upgraded.execute("PRAGMA table_info(matches)")
            if column["name"] == "location"
        )["notnull"]
        == 0
    )
    upgraded.close()
