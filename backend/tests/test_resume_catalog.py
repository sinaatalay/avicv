"""Grouped freshness tracks every posting without changing saved score metadata."""

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.config import Settings
from backend.db import connect_app
from backend.jobs import store
from backend.matching.jev import JobScore
from backend.resumes.catalog import catalog_states
from backend.resumes.pipeline import save_score
from backend.resumes.storage import job_key


@pytest.fixture
def app_conn(settings: Settings) -> Iterator[sqlite3.Connection]:
    conn = connect_app(settings.app_db)
    conn.execute(
        "INSERT INTO resumes (id, created_at, pdf_sha256, status) VALUES ('r', 'now', 'hash', 'done')"
    )
    conn.commit()
    yield conn
    conn.close()


def member(index: int) -> dict[str, Any]:
    return {
        "id": index,
        "provider": "greenhouse",
        "board": "acme",
        "source_id": str(index),
        "content_hash": f"hash-{index}",
    }


def save(
    conn: sqlite3.Connection,
    title: str,
    members: list[dict[str, Any]],
    *,
    generation: int = 1,
) -> str:
    role: dict[str, Any] = members[0] | {
        "title": title,
        "locations": ["Toronto", "Remote Canada"],
        "description_text": "Original Python role",
        "postings": members,
    }
    score = JobScore(
        role["id"], role["content_hash"], 0.8, 3, 0.8, 0.8, 0.8, "matches", 100
    )
    save_score(conn, "r", 0, generation, role, score)
    conn.commit()
    return job_key(role)


def seed_catalog(settings: Settings, count: int = 2) -> None:
    conn = store.connect(settings.jobs_db)
    now = datetime.now(UTC).isoformat()
    for index in range(1, count + 1):
        conn.execute(
            "INSERT INTO jobs (id, provider, board, source_id, first_seen, last_seen, content_hash, content_updated_at, title, raw_zstd) VALUES (?, 'greenhouse', 'acme', ?, ?, ?, ?, ?, 'Current title', ?)",
            (index, str(index), now, now, f"hash-{index}", now, b"fixture"),
        )
    store.mark_not_modified(conn, "greenhouse", "acme", now)
    conn.close()


@pytest.mark.parametrize(
    "change, expected",
    [
        ("UPDATE jobs SET content_hash = 'changed' WHERE id = 2", "changed"),
        ("UPDATE jobs SET closed_at = 'now' WHERE id = 2", "changed"),
        ("UPDATE jobs SET closed_at = 'now'", "closed"),
        ("DELETE FROM jobs WHERE id = 2", "missing"),
        ("DELETE FROM jobs", "missing"),
    ],
)
def test_every_group_member_controls_catalog_status(
    settings: Settings, app_conn: sqlite3.Connection, change: str, expected: str
) -> None:
    seed_catalog(settings)
    key = save(app_conn, "Original title", [member(1), member(2)])
    snapshot = app_conn.execute(
        "SELECT job_snapshot FROM matches WHERE job_key = ?", (key,)
    ).fetchone()[0]
    assert catalog_states(app_conn, "r", 1, settings)[key]["catalog_status"] == "open"
    conn = store.connect(settings.jobs_db)
    conn.execute(change)
    conn.close()
    assert catalog_states(app_conn, "r", 1, settings)[key]["catalog_status"] == expected
    assert (
        app_conn.execute(
            "SELECT job_snapshot FROM matches WHERE job_key = ?", (key,)
        ).fetchone()[0]
        == snapshot
    )


def test_missing_member_cannot_make_surviving_closed_member_hide_group(
    settings: Settings, app_conn: sqlite3.Connection
) -> None:
    seed_catalog(settings, 1)
    key = save(app_conn, "Role", [member(1), member(2)])
    conn = store.connect(settings.jobs_db)
    conn.execute("UPDATE jobs SET closed_at = 'now'")
    conn.close()
    assert (
        catalog_states(app_conn, "r", 1, settings)[key]["catalog_status"] == "missing"
    )


def test_numeric_id_reuse_cannot_substitute_a_different_source(
    settings: Settings, app_conn: sqlite3.Connection
) -> None:
    seed_catalog(settings, 1)
    key = save(app_conn, "Original", [member(1)])
    conn = store.connect(settings.jobs_db)
    conn.execute("UPDATE jobs SET source_id = 'replacement'")
    conn.close()
    state = catalog_states(app_conn, "r", 1, settings)[key]
    assert state["catalog_status"] == "missing"
    assert state["source_stale"] is True


def test_legacy_representative_snapshot_and_unverified_rows(
    settings: Settings, app_conn: sqlite3.Connection
) -> None:
    seed_catalog(settings, 1)
    old = save(app_conn, "Legacy snapshot", [member(1)])
    unverified = save(app_conn, "No original snapshot", [member(1)])
    original = json.loads(
        app_conn.execute(
            "SELECT job_snapshot FROM matches WHERE job_key = ?", (old,)
        ).fetchone()[0]
    )
    del original["postings"]
    app_conn.execute(
        "UPDATE matches SET job_snapshot = ? WHERE job_key = ?",
        (json.dumps(original), old),
    )
    app_conn.execute(
        "UPDATE matches SET job_snapshot = NULL WHERE job_key = ?", (unverified,)
    )
    states = catalog_states(app_conn, "r", 1, settings)
    assert states[old]["catalog_status"] == "open"
    assert states[unverified] == {
        "catalog_status": "unverified",
        "source_status": "unknown",
        "source_verified_at": None,
        "source_stale": True,
    }


def test_source_failure_marks_snapshot_stale_without_changing_content_status(
    settings: Settings, app_conn: sqlite3.Connection
) -> None:
    seed_catalog(settings)
    key = save(app_conn, "Role", [member(1), member(2)])
    fresh = catalog_states(app_conn, "r", 1, settings)[key]
    assert fresh["source_stale"] is False
    conn = store.connect(settings.jobs_db)
    store.mark_failed(conn, "greenhouse", "acme", datetime.now(UTC).isoformat())
    conn.close()
    state = catalog_states(app_conn, "r", 1, settings)[key]
    assert state["catalog_status"] == "open"
    assert state["source_status"] == "failed"
    assert state["source_stale"] is True
    assert state["source_verified_at"] == fresh["source_verified_at"]


def test_himalayas_freshness_uses_each_posting_last_seen(
    settings: Settings, app_conn: sqlite3.Connection
) -> None:
    seed_catalog(settings)
    members = [member(i) | {"provider": "himalayas"} for i in (1, 2)]
    key = save(app_conn, "Search role", members)
    conn = store.connect(settings.jobs_db)
    conn.execute("UPDATE jobs SET provider = 'himalayas'")
    old = (datetime.now(UTC) - timedelta(days=4)).isoformat()
    conn.execute("UPDATE jobs SET last_seen = ? WHERE id = 2", (old,))
    run = store.start_run(conn, datetime.now(UTC).isoformat(), "test")
    store.log_board(
        conn,
        run,
        {
            "provider": "himalayas",
            "board": "*",
            "unit": "test",
            "status": "ok",
            "complete": 0,
        },
    )
    conn.close()
    state = catalog_states(app_conn, "r", 1, settings)[key]
    assert state["catalog_status"] == "open"
    assert state["source_status"] == "ok"
    assert state["source_stale"] is True
    assert state["source_verified_at"] == old


def test_members_are_batched_and_other_generations_are_excluded(
    settings: Settings, app_conn: sqlite3.Connection
) -> None:
    seed_catalog(settings, 450)
    key = save(app_conn, "Large group", [member(i) for i in range(1, 451)])
    save(app_conn, "Old generation", [member(1)], generation=0)
    states = catalog_states(app_conn, "r", 1, settings)
    assert list(states) == [key]
    assert states[key]["catalog_status"] == "open"


def test_missing_catalog_and_missing_metadata_preserve_readable_matches(
    settings: Settings, app_conn: sqlite3.Connection, jobs_db: Path
) -> None:
    # conftest's minimal schema has jobs/FTS but no board or ingestion metadata.
    key = save(
        app_conn, "Role", [member(1) | {"board": "stripe", "content_hash": "hash1"}]
    )
    state = catalog_states(app_conn, "r", 1, settings)[key]
    assert state["catalog_status"] == "open"
    assert state["source_status"] == "unknown" and state["source_stale"]
    jobs_db.unlink()
    assert (
        catalog_states(app_conn, "r", 1, settings)[key]["catalog_status"] == "missing"
    )
