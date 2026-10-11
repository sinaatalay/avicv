"""The ingester's HTTP client: retries, Retry-After, politeness limits, the run deadline."""

import asyncio
import email.utils
import itertools
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2 as httpx
import pytest

from backend.jobs import http as jobs_http
from backend.jobs.http import DeadlineExceeded, Fetcher, FetchError, parse_retry_after

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Backoff delays, recorded instead of waited for."""
    recorded: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(jobs_http.asyncio, "sleep", fake_sleep)
    return recorded


def fetcher(handler: Callable[[httpx.Request], Any], **kwargs: Any) -> Fetcher:
    return Fetcher(transport=httpx.MockTransport(handler), min_gap=0, **kwargs)


async def test_429_waits_for_retry_after(sleeps: list[float]) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"ok": True})

    async with fetcher(handler) as f:
        r = await f.get_json("https://a.test/jobs")
    assert (r.status, r.data, r.attempts) == (200, {"ok": True}, 2)
    assert sleeps == [7.0]
    assert f.stats["a.test"].retries == 1


async def test_retry_after_beyond_cap_fails_fast(sleeps: list[float]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, headers={"Retry-After": "3600"})

    async with fetcher(handler, max_retry_after=60) as f:
        with pytest.raises(FetchError, match="too long"):
            await f.get_json("https://a.test/jobs")
    assert sleeps == []


def test_retry_after_parsing() -> None:
    soon = email.utils.format_datetime(
        datetime.now(UTC) + timedelta(seconds=30), usegmt=True
    )
    delay = parse_retry_after(soon)
    assert delay is not None
    assert 25 <= delay <= 30
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after("soon") is None
    assert parse_retry_after(None) is None


async def test_5xx_backs_off_exponentially(sleeps: list[float]) -> None:
    statuses = iter([503, 502, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(statuses), json={})

    async with fetcher(handler, backoff_base=1.0) as f:
        r = await f.get_json("https://a.test/jobs")
    assert r.attempts == 3
    assert 0.5 <= sleeps[0] <= 1.5  # 1 s, jittered
    assert 1.0 <= sleeps[1] <= 3.0  # 2 s, jittered


async def test_gives_up_after_max_attempts(sleeps: list[float]) -> None:
    async with fetcher(lambda request: httpx.Response(500), max_attempts=3) as f:
        with pytest.raises(FetchError, match="gave up after 3 attempts") as caught:
            await f.get_json("https://a.test/jobs")
    assert caught.value.status == 500
    assert len(sleeps) == 2


async def test_connection_errors_are_retried(sleeps: list[float]) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json=[])

    async with fetcher(handler) as f:
        r = await f.get_json("https://a.test/jobs")
    assert (r.status, r.attempts) == (200, 3)


@pytest.mark.parametrize("status", [304, 404, 410])
async def test_other_statuses_are_returned_not_retried(
    sleeps: list[float], status: int
) -> None:
    async with fetcher(
        lambda request: httpx.Response(status, headers={"ETag": '"x"'})
    ) as f:
        r = await f.get_json("https://a.test/jobs")
    assert (r.status, r.attempts, r.data) == (status, 1, None)
    assert r.etag == '"x"'
    assert sleeps == []


async def test_concurrency_is_bounded_per_host_and_overall() -> None:
    live: defaultdict[str, int] = defaultdict(int)
    peak: defaultdict[str, int] = defaultdict(int)

    async def handler(request: httpx.Request) -> httpx.Response:
        for key in (request.url.host, "*"):
            live[key] += 1
            peak[key] = max(peak[key], live[key])
        await asyncio.sleep(0.01)
        for key in (request.url.host, "*"):
            live[key] -= 1
        return httpx.Response(200, json={})

    async with fetcher(handler, max_concurrency=8, per_host=2) as f:
        urls = [f"https://h{i}.test/{j}" for i in range(6) for j in range(6)]
        await asyncio.gather(*(f.get_json(url) for url in urls))
    assert max(n for host, n in peak.items() if host != "*") == 2
    assert peak["*"] == 8


async def test_requests_to_one_host_are_spaced() -> None:
    starts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        starts.append(time.monotonic())
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    async with Fetcher(
        transport=transport, host_limits={"p.test": 1}, host_gaps={"p.test": 0.05}
    ) as f:
        await asyncio.gather(*(f.get_json(f"https://p.test/{i}") for i in range(4)))
    assert min(b - a for a, b in itertools.pairwise(starts)) >= 0.045


async def test_deadline_stops_new_requests() -> None:
    async with fetcher(
        lambda request: httpx.Response(200), deadline=time.monotonic() - 1
    ) as f:
        with pytest.raises(DeadlineExceeded):
            await f.get_json("https://a.test/jobs")


async def test_deadline_cancels_inflight_and_queued_requests() -> None:
    live = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal live
        live += 1
        try:
            await asyncio.Event().wait()
            return httpx.Response(200)
        finally:
            live -= 1

    started = time.monotonic()
    async with fetcher(handler, per_host=1, deadline=started + 0.03) as f:
        results = await asyncio.gather(
            *(f.get_json(f"https://a.test/{i}") for i in range(3)),
            return_exceptions=True,
        )
    assert all(isinstance(result, DeadlineExceeded) for result in results)
    assert live == 0
    assert time.monotonic() - started < 0.5


async def test_totals_count_attempts_and_bytes(sleeps: list[float]) -> None:
    statuses = iter([503, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(statuses), json={"jobs": []})

    async with fetcher(handler, backoff_base=0.01) as f:
        await f.get_json("https://a.test/jobs")
        totals = f.totals()
    assert (totals["requests"], totals["retries"]) == (2, 1)
    assert totals["bytes_decoded"] > 0
    assert totals["by_host"]["a.test"]["statuses"] == {"503": 1, "200": 1}
