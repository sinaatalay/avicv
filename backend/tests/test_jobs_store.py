"""jobs.db rules: idempotent upserts, safe closures, reopening, FTS upkeep, schema contract."""

import sqlite3
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from backend.jobs import normalize, store

T0, T1, T2, T3 = (f"2026-10-1{d}T00:00:00+00:00" for d in range(4))


@pytest.fixture
def conn(tmp_path: Path) -> Generator[sqlite3.Connection]:
    connection = store.connect(tmp_path / "jobs.db")
    yield connection
    connection.close()


def job(
    source_id: str, title: str = "Backend Engineer", body: str = "python postgres"
) -> dict[str, Any]:
    return {"id": source_id, "title": title, "body": body}


def items(*jobs: dict[str, Any]) -> list[tuple[str, Any, str]]:
    return [(j["id"], j, normalize.content_hash(j)) for j in jobs]


def build(raw: Any, chash: str) -> dict[str, Any]:
    record = {
        "source_id": raw["id"],
        "company": "Acme",
        "title": raw["title"],
        "locations": ["Calgary, AB"],
        "country_hints": [],
        "workplace_type": None,
        "description_html": f"<p>{raw['body']}</p>",
    }
    return normalize.to_row("greenhouse", "acme", raw, record, chash)


def apply(
    conn: sqlite3.Connection,
    jobs: list[dict[str, Any]],
    now: str,
    *,
    complete: bool = True,
) -> store.Applied:
    return store.apply_board(
        conn, "greenhouse", "acme", items(*jobs), build, now, complete=complete
    )


def fts(conn: sqlite3.Connection, query: str) -> int:
    return conn.execute(
        "SELECT count(*) FROM jobs_fts WHERE jobs_fts MATCH ?", (query,)
    ).fetchone()[0]


def get(conn: sqlite3.Connection, source_id: str) -> sqlite3.Row:
    return conn.execute(
        "SELECT * FROM jobs WHERE source_id = ?", (source_id,)
    ).fetchone()


def board_state(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute(
        "SELECT * FROM board_state WHERE provider = 'greenhouse' AND board = 'acme'"
    ).fetchone()


def test_insert_indexes_for_search(conn: sqlite3.Connection) -> None:
    result = apply(
        conn, [job("1"), job("2", body="react"), job("3", body="python spark")], T0
    )
    assert (result.status, result.new, result.seen) == ("ok", 3, 3)
    assert fts(conn, "python") == 2
    assert get(conn, "1")["first_seen"] == T0
    assert board_state(conn)["last_verified_at"] == T0


def test_unchanged_jobs_are_not_written(conn: sqlite3.Connection) -> None:
    jobs = [job("1"), job("2", body="react")]
    apply(conn, jobs, T0)
    before = conn.total_changes
    result = apply(conn, jobs, T1)
    assert (result.new, result.changed, result.unchanged) == (0, 0, 2)
    assert conn.total_changes - before == 1  # only the board_state row
    assert get(conn, "1")["last_seen"] == T0
    assert get(conn, "1")["content_updated_at"] == T0
    assert (
        board_state(conn)["last_verified_at"] == T1
    )  # all open jobs were listed at T1


def test_partial_fetch_never_closes(conn: sqlite3.Connection) -> None:
    apply(conn, [job("1"), job("2"), job("3")], T0)
    result = apply(conn, [job("1")], T1, complete=False)
    assert (result.status, result.closed) == ("partial", 0)
    assert get(conn, "3")["closed_at"] is None
    assert board_state(conn)["last_verified_at"] == T0


def test_complete_fetch_closes_missing_jobs(conn: sqlite3.Connection) -> None:
    apply(conn, [job("1"), job("2"), job("3")], T0)
    result = apply(conn, [job("1"), job("2")], T1)
    assert result.closed == 1
    assert get(conn, "3")["closed_at"] == T1


def test_empty_board_needs_a_second_complete_fetch(conn: sqlite3.Connection) -> None:
    apply(conn, [job("1"), job("2")], T0)
    first = apply(conn, [], T1)
    assert (first.status, first.closed) == ("suspect", 0)
    assert board_state(conn)["suspect_since"] == T1
    second = apply(conn, [], T2)
    assert (second.status, second.closed) == ("ok", 2)
    assert board_state(conn)["suspect_since"] is None


def test_mass_drop_needs_a_second_complete_fetch(conn: sqlite3.Connection) -> None:
    apply(conn, [job(str(i)) for i in range(25)], T0)
    first = apply(conn, [job(str(i)) for i in range(5)], T1)
    assert (first.status, first.closed) == ("suspect", 0)
    assert board_state(conn)["last_verified_at"] == T0
    second = apply(conn, [job(str(i)) for i in range(5)], T2)
    assert second.closed == 20


def test_suspicion_clears_when_the_board_recovers(conn: sqlite3.Connection) -> None:
    jobs = [job("1"), job("2")]
    apply(conn, jobs, T0)
    assert apply(conn, [], T1).status == "suspect"
    assert apply(conn, jobs, T2).closed == 0
    assert board_state(conn)["suspect_since"] is None
    assert apply(conn, [], T3).status == "suspect"  # a new drop is suspected afresh


def test_changed_content_is_rewritten_and_reindexed(conn: sqlite3.Connection) -> None:
    apply(conn, [job("1")], T0)
    result = apply(
        conn, [job("1", title="Senior Backend Engineer", body="golang postgres")], T1
    )
    assert result.changed == 1
    row = get(conn, "1")
    assert (row["title"], row["content_updated_at"], row["first_seen"]) == (
        "Senior Backend Engineer",
        T1,
        T0,
    )
    assert fts(conn, "golang") == 1
    assert fts(conn, "python") == 0


def test_closed_job_reopens(conn: sqlite3.Connection) -> None:
    apply(conn, [job("1"), job("2")], T0)
    apply(conn, [job("1")], T1)
    assert get(conn, "2")["closed_at"] == T1
    result = apply(conn, [job("1"), job("2")], T2)
    assert (result.reopened, result.unchanged) == (1, 2)
    assert get(conn, "2")["closed_at"] is None
    apply(conn, [job("1")], T3)
    changed = apply(
        conn, [job("1"), job("2", body="rust")], "2026-10-14T00:00:00+00:00"
    )
    assert (changed.reopened, changed.changed) == (1, 1)
    assert get(conn, "2")["closed_at"] is None


def test_not_modified_only_updates_the_board(conn: sqlite3.Connection) -> None:
    apply(conn, [job("1"), job("2")], T0)
    before = conn.total_changes
    assert store.mark_not_modified(conn, "greenhouse", "acme", T1) == 2
    assert conn.total_changes - before == 1
    state = board_state(conn)
    assert (state["last_status"], state["last_verified_at"]) == ("not_modified", T1)


def test_search_sourced_jobs_expire_instead(conn: sqlite3.Connection) -> None:
    def himalayas_row(raw: Any, chash: str) -> dict[str, Any]:
        return build(raw, chash) | {
            "provider": "himalayas",
            "board": "volume7",
            "expires_at": raw["expires"],
        }

    def seen(jobs: list[dict[str, Any]], now: str) -> store.Applied:
        return store.apply_board(
            conn,
            "himalayas",
            "volume7",
            items(*jobs),
            himalayas_row,
            now,
            complete=False,
            track_seen=True,
        )

    fresh = job("a") | {"expires": "2026-12-01T00:00:00+00:00"}
    stale = job("b") | {"expires": "2026-12-01T00:00:00+00:00"}
    expired = job("c") | {"expires": "2026-10-12T12:00:00+00:00"}
    seen([fresh, stale, expired], T0)
    assert seen([fresh], T3).closed == 0  # absent from a search: not closed
    assert get(conn, "a")["last_seen"] == T3  # bumped although unchanged
    assert store.expire_unseen(conn, "himalayas", T3, unseen_since=T2) == 2
    assert get(conn, "a")["closed_at"] is None
    assert get(conn, "b")["closed_at"] == T3  # not seen since T0
    assert get(conn, "c")["closed_at"] == T3  # past its expiry


def test_raw_payload_round_trips(conn: sqlite3.Connection) -> None:
    payload = job("1", body="x " * 2000)
    apply(conn, [payload], T0)
    assert store.stored_raw(conn, "greenhouse", "acme") == {"1": payload}
    blob = get(conn, "1")["raw_zstd"]
    assert len(blob) < len(normalize.canonical_json(payload)) / 10


def test_fts_survives_vacuum(conn: sqlite3.Connection) -> None:
    apply(conn, [job(str(i), body=f"word{i}") for i in range(50)], T0)
    apply(conn, [job(str(i), body=f"word{i}") for i in range(25, 50)], T1)
    conn.execute("VACUUM")
    row = conn.execute(
        "SELECT j.source_id FROM jobs_fts JOIN jobs AS j ON j.id = jobs_fts.rowid WHERE jobs_fts MATCH 'word42'"
    ).fetchone()
    assert row["source_id"] == "42"
    conn.execute("INSERT INTO jobs_fts (jobs_fts) VALUES ('integrity-check')")


def test_schema_contract(conn: sqlite3.Connection) -> None:
    """Columns the matching code reads; changing them needs a heads-up."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    assert columns >= {
        "id", "provider", "board", "source_id", "company", "title", "locations_text", "geo_bucket",
        "workplace_type", "employment_type", "posted_at", "apply_url", "source_url", "salary_min",
        "salary_max", "salary_currency", "salary_interval", "description_text", "content_hash",
        "closed_at", "first_seen",
    }  # fmt: skip
    fts_columns = [row["name"] for row in conn.execute("PRAGMA table_info(jobs_fts)")]
    assert fts_columns == ["title", "company", "locations_text", "description_text"]
    run_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(ingest_runs)")
    }
    assert run_columns >= {"status", "started_at", "finished_at"}


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "jobs.db"
    sqlite3.connect(path).execute("PRAGMA user_version = 99").connection.close()
    with pytest.raises(RuntimeError, match="schema version 99"):
        store.connect(path)


def test_runs_and_abandoned_runs(conn: sqlite3.Connection) -> None:
    crashed = store.start_run(conn, T0, "cli")
    run = store.start_run(conn, T1, "cli")  # holding the lock: the old one was killed
    status = dict(conn.execute("SELECT run_id, status FROM ingest_runs").fetchall())
    assert status == {crashed: "abandoned", run: "running"}
    assert store.last_success_started_at(conn) is None
    store.finish_run(conn, run, {"finished_at": T1, "status": "partial"})
    assert store.last_success_started_at(conn) is None
    store.finish_run(conn, run, {"finished_at": T1, "status": "ok"})
    assert store.last_success_started_at(conn) == T1


def test_mass_drop_confirmation_requires_the_same_missing_jobs(
    conn: sqlite3.Connection,
) -> None:
    jobs = [job(str(i)) for i in range(25)]
    apply(conn, jobs, T0)
    assert apply(conn, jobs[:5], T1).status == "suspect"
    # The count is identical, but these are different survivors. This is not confirmation.
    assert apply(conn, jobs[5:10], T2).status == "suspect"
    assert (
        conn.execute(
            "SELECT count(*) FROM jobs WHERE closed_at IS NOT NULL"
        ).fetchone()[0]
        == 0
    )
    assert apply(conn, jobs[5:10], T3).closed == 20


def test_legacy_schema_upgrade_preserves_job_identity_and_requires_new_confirmation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "jobs.db"
    connection = store.connect(path)
    apply(connection, [job("1")], T0)
    apply(connection, [], T1)
    before = dict(get(connection, "1"))
    for table, column in (
        ("board_state", "suspect_missing"),
        ("board_state", "configured"),
        ("http_cache", "provider"),
        ("http_cache", "board"),
        ("ingest_runs", "scope_key"),
    ):
        connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    connection.execute(
        "INSERT INTO http_cache VALUES ('https://example.test', 'old', '1', ?)", (T0,)
    )
    connection.execute("PRAGMA user_version = 1")
    connection.close()

    upgraded = store.connect(path)
    try:
        assert dict(get(upgraded, "1")) == before
        assert upgraded.execute("SELECT count(*) FROM http_cache").fetchone()[0] == 0
        assert apply(upgraded, [], T2).status == "suspect"
        assert apply(upgraded, [], T3).closed == 1
        upgraded.execute("INSERT INTO jobs_fts (jobs_fts) VALUES ('integrity-check')")
    finally:
        upgraded.close()


def test_scope_and_failed_retry_do_not_reuse_old_success(
    conn: sqlite3.Connection,
) -> None:
    run = store.start_run(conn, T0, "cli", "greenhouse-only")
    store.finish_run(conn, run, {"finished_at": T1, "status": "ok"})
    assert store.last_success_started_at(conn, "greenhouse-only") == T0
    assert store.last_success_started_at(conn, "all-providers") is None
    retry = store.start_run(conn, T2, "cli", "greenhouse-only")
    store.finish_run(conn, retry, {"finished_at": T3, "status": "failed"})
    assert store.last_success_started_at(conn, "greenhouse-only") is None


def test_validator_replacement_removes_obsolete_pages_and_missing_etags(
    conn: sqlite3.Connection,
) -> None:
    store.save_validators(
        conn, {"page1": "v1", "page2": "v2"}, "1", T0, "lever", "acme"
    )
    store.save_validators(conn, {"page1": "v3"}, "1", T1, "lever", "acme")
    assert store.load_validators(conn, "1") == {"page1": "v3"}
    store.save_validators(conn, {}, "1", T2, "lever", "acme")
    assert store.load_validators(conn, "1") == {}


def test_removed_source_is_explicitly_stale_without_closing_jobs(
    conn: sqlite3.Connection,
) -> None:
    apply(conn, [job("1")], T0)
    store.reconcile_boards(conn, [])
    assert get(conn, "1")["closed_at"] is None
    health = store.source_health(conn)[("greenhouse", "acme")]
    assert (health["status"], health["configured"], health["stale"]) == (
        "unconfigured",
        False,
        True,
    )
