"""`jobs-ingest` end to end, against fake provider APIs that serve tests/fixtures/jobs."""

import asyncio
import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from backend.config import get_settings
from backend.jobs.ingest import Options, ingest, parse_args, run_lock, run_once

FIXTURES = Path(__file__).parent / "fixtures" / "jobs"
GREENHOUSE = "boards-api.greenhouse.io/v1/boards/acme/jobs"
SR_LIST = "api.smartrecruiters.com/v1/companies/Acme/postings"
BOARDS = """
[[board]]
provider = "greenhouse"
id = "acme"
company = "Acme"

[[board]]
provider = "ashby"
id = "acme"
company = "Acme"

[[board]]
provider = "lever"
id = "acme"
company = "Acme"

[[board]]
provider = "smartrecruiters"
id = "Acme"
company = "Acme"
countries = ["us", "ca"]
"""
SEARCHES = 'countries = ["CA"]\npages = 2\nqueries = ["python"]\n'


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


class FakeSources:
    """The provider APIs as far as the ingester sees them, ETags and 304s included."""

    def __init__(self) -> None:
        self.payloads: dict[str, Any] = {
            GREENHOUSE: load("greenhouse.json"),
            "api.ashbyhq.com/posting-api/job-board/acme": load("ashby.json"),
            "api.lever.co/v0/postings/acme": load("lever.json"),
            SR_LIST: load("smartrecruiters_list.json"),
            "himalayas.app/jobs/api/search": load("himalayas.json"),
        }
        self.details = load("smartrecruiters_detail.json")
        self.calls: Counter[str] = Counter()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.host + request.url.path
        self.calls["sr_detail" if path.startswith(SR_LIST + "/") else path] += 1
        if path.startswith(SR_LIST + "/"):
            return httpx.Response(200, json=self.details[path.rsplit("/", 1)[1]])
        payload = self.payloads[path]
        params = request.url.params
        if path == SR_LIST and params.get("country") == "ca":
            payload = {"offset": 0, "limit": 100, "totalFound": 0, "content": []}
        if params.get("skip", "0") != "0" or params.get("page", "1") != "1":
            payload = [] if isinstance(payload, list) else payload | {"jobs": []}
        body = json.dumps(payload).encode()
        etag = f'W/"{hashlib.sha256(body).hexdigest()[:16]}"'
        if request.headers.get("If-None-Match") == etag:
            return httpx.Response(304, headers={"ETag": etag})
        return httpx.Response(
            200,
            content=body,
            headers={"ETag": etag, "Content-Type": "application/json"},
        )


@pytest.fixture
def opts(tmp_path: Path) -> Options:
    (tmp_path / "boards.toml").write_text(BOARDS)
    (tmp_path / "himalayas.toml").write_text(SEARCHES)
    return Options(
        db=tmp_path / "data" / "jobs.db",
        boards=tmp_path / "boards.toml",
        himalayas=tmp_path / "himalayas.toml",
        min_interval_hours=6,
    )


def query(opts: Options, sql: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(opts.db)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_ingest_end_to_end(opts: Options) -> None:
    sources = FakeSources()
    transport = httpx.MockTransport(sources)

    first = run_once(opts, transport)
    assert first["status"] == "ok"
    # 3 Greenhouse + 2 listed Ashby (1 unlisted) + 2 Lever + 2 SmartRecruiters + 2 Himalayas
    assert first["jobs_new"] == 11
    assert first["boards"] == {"ok": 4, "search_ok": 1}
    assert sources.calls["sr_detail"] == 2
    rows = query(
        opts,
        "SELECT provider, count(*) AS n FROM jobs WHERE closed_at IS NULL GROUP BY provider",
    )
    assert {r["provider"]: r["n"] for r in rows} == {
        "greenhouse": 3,
        "ashby": 2,
        "lever": 2,
        "smartrecruiters": 2,
        "himalayas": 2,
    }
    described = query(
        opts, "SELECT count(*) AS n FROM jobs WHERE description_text IS NOT NULL"
    )
    assert described[0]["n"] == 11
    etags = query(opts, "SELECT url FROM http_cache")
    assert len(etags) == 3  # Greenhouse, Ashby and Lever: one page each

    # Within the refresh interval: no requests at all
    requests = sum(sources.calls.values())
    assert "skipped" in run_once(opts, transport)
    assert sum(sources.calls.values()) == requests

    # Forced, unchanged: the ATS boards answer 304, details are reused, nothing is written
    second = run_once(replace(opts, force=True), transport)
    assert second["boards"] == {"not_modified": 3, "ok": 1, "search_ok": 1}
    assert (second["jobs_new"], second["jobs_changed"], second["jobs_closed"]) == (
        0,
        0,
        0,
    )
    assert sources.calls["sr_detail"] == 2
    states = query(
        opts,
        "SELECT provider, last_status FROM board_state WHERE provider != 'himalayas'",
    )
    assert {r["provider"]: r["last_status"] for r in states} == {
        "greenhouse": "not_modified",
        "ashby": "not_modified",
        "lever": "not_modified",
        "smartrecruiters": "ok",
    }

    # A posting disappears from Greenhouse: closed on the next complete fetch
    board = sources.payloads[GREENHOUSE]
    sources.payloads[GREENHOUSE] = {"jobs": board["jobs"][:2], "meta": {"total": 2}}
    third = run_once(replace(opts, force=True), transport)
    assert third["jobs_closed"] == 1
    closed = query(opts, "SELECT source_id FROM jobs WHERE closed_at IS NOT NULL")
    assert [r["source_id"] for r in closed] == [str(board["jobs"][2]["id"])]

    runs = query(opts, "SELECT status, finished_at FROM ingest_runs ORDER BY run_id")
    assert [r["status"] for r in runs] == ["ok", "ok", "ok"]
    assert all(r["finished_at"] for r in runs)


def test_failing_board_fails_alone(opts: Options) -> None:
    sources = FakeSources()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.lever.co":
            return httpx.Response(
                404, json={"ok": False, "error": "Document not found"}
            )
        return sources(request)

    summary = run_once(opts, httpx.MockTransport(handler))
    assert summary["status"] == "partial"
    assert summary["boards"]["failed"] == 1
    assert summary["errors"][0]["provider"] == "lever"
    state = query(opts, "SELECT last_status FROM board_state WHERE provider = 'lever'")
    assert state[0]["last_status"] == "failed"


def test_lock_keeps_runs_apart(opts: Options) -> None:
    with run_lock(opts.db) as locked:
        assert locked
        assert "skipped" in run_once(opts, httpx.MockTransport(FakeSources()))


def test_cli_defaults_come_from_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JOBS_REFRESH_HOURS", "3")
    get_settings.cache_clear()
    try:
        args = parse_args([])
        assert args.db == tmp_path / "jobs.db"
        assert args.min_interval_hours == 3
        assert not args.force
        assert not args.loop
        assert args.loop_minutes == 15
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    "args",
    [
        ["--loop-minutes", "0"],
        ["--loop-minutes", "nan"],
        ["--budget-seconds", "0"],
        ["--budget-seconds", "inf"],
        ["--min-interval-hours", "-1"],
        ["--himalayas-ttl-days", "0"],
        ["--sr-detail-cap", "-1"],
    ],
)
def test_cli_rejects_unsafe_limits(args: list[str]) -> None:
    with pytest.raises(SystemExit):
        parse_args(args)


def test_himalayas_failure_is_a_failed_run_and_retries_without_force(
    opts: Options,
) -> None:
    opts = replace(opts, only=frozenset({"himalayas"}))
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    for _ in range(2):
        result = run_once(opts, transport)
        assert result["status"] == "failed"
        assert result["boards"]["search_failed"] == 1
    assert calls == 2
    assert query(opts, "SELECT boards_failed FROM ingest_runs")[0]["boards_failed"] == 1


def test_provider_limited_success_does_not_suppress_a_full_run(opts: Options) -> None:
    sources = FakeSources()
    transport = httpx.MockTransport(sources)
    assert (
        run_once(replace(opts, only=frozenset({"greenhouse"})), transport)["status"]
        == "ok"
    )
    result = run_once(opts, transport)
    assert result["status"] == "ok"
    assert sources.calls[SR_LIST] > 0


def test_pending_smartrecruiters_details_are_partial_and_retryable(
    opts: Options,
) -> None:
    sources = FakeSources()
    transport = httpx.MockTransport(sources)
    limited = replace(opts, only=frozenset({"smartrecruiters"}), sr_detail_cap=0)
    first = run_once(limited, transport)
    assert first["status"] == "partial"
    state = query(opts, "SELECT last_status FROM board_state")[0]
    assert state["last_status"] == "partial"
    assert run_once(replace(limited, sr_detail_cap=50), transport)["status"] == "ok"
    assert sources.calls["sr_detail"] == 2


def test_suspect_inventory_clears_etag_and_needs_identical_confirmation(
    opts: Options,
) -> None:
    sources = FakeSources()
    transport = httpx.MockTransport(sources)
    opts = replace(opts, only=frozenset({"greenhouse"}))
    assert run_once(opts, transport)["status"] == "ok"
    sources.payloads[GREENHOUSE] = {"jobs": [], "meta": {"total": 0}}
    suspect = run_once(replace(opts, force=True), transport)
    assert suspect["status"] == "partial"
    assert query(opts, "SELECT count(*) n FROM http_cache")[0]["n"] == 0
    confirmed = run_once(opts, transport)
    assert confirmed["status"] == "ok"
    assert confirmed["jobs_closed"] == 3


def test_himalayas_outage_does_not_expire_jobs_by_absence(opts: Options) -> None:
    sources = FakeSources()
    opts = replace(opts, only=frozenset({"himalayas"}))
    run_once(opts, httpx.MockTransport(sources))
    conn = sqlite3.connect(opts.db)
    conn.execute(
        "UPDATE jobs SET last_seen = '2020-01-01T00:00:00+00:00', expires_at = NULL"
    )
    conn.commit()
    conn.close()
    run_once(
        replace(opts, force=True),
        httpx.MockTransport(lambda request: httpx.Response(404)),
    )
    assert (
        query(opts, "SELECT count(*) n FROM jobs WHERE closed_at IS NULL")[0]["n"] == 2
    )


def test_budget_cancels_inflight_requests_and_finishes_run(opts: Options) -> None:
    active = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active
        active += 1
        try:
            await asyncio.Event().wait()
            return httpx.Response(200)
        finally:
            active -= 1

    result = run_once(replace(opts, budget_seconds=0.05), httpx.MockTransport(handler))
    assert result["status"] == "failed"
    assert result["wall_seconds"] < 1
    assert active == 0
    assert query(opts, "SELECT status FROM ingest_runs")[0]["status"] == "failed"


def test_external_cancellation_cleans_up_requests_and_run(opts: Options) -> None:
    active = 0

    async def scenario() -> None:
        began = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal active
            active += 1
            began.set()
            try:
                await asyncio.Event().wait()
                return httpx.Response(200)
            finally:
                active -= 1

        task = asyncio.create_task(ingest(opts, httpx.MockTransport(handler)))
        await began.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert active == 0
    assert query(opts, "SELECT status FROM ingest_runs")[0]["status"] == "failed"
