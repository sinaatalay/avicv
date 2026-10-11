"""Polite async HTTP for the job sources.

Bounded concurrency (overall and per host), per-host pacing, timeouts, and retries with
exponential backoff that honor `Retry-After`. Uses httpx2, Pydantic's maintained
continuation of httpx.
"""

import asyncio
import email.utils
import json
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Self
from urllib.parse import urlsplit

import httpx2 as httpx

USER_AGENT = "speedhack/0.1 (job ingestion)"
RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class DeadlineExceeded(Exception):
    """The run's time budget ran out before a request could be sent or retried."""


class FetchError(Exception):
    """A request that kept failing, or whose server asked us to come back too late."""

    def __init__(self, url: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{message} <{url}>")
        self.url = url
        self.status = status


@dataclass
class HostStats:
    requests: int = 0  # attempts sent, including retries
    retries: int = 0
    statuses: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    bytes_wire: int = 0  # as received, before decompression
    bytes_decoded: int = 0
    request_seconds: float = 0.0


@dataclass
class Response:
    url: str
    status: int
    data: Any  # parsed JSON, or None when the body isn't JSON
    bytes_wire: int
    bytes_decoded: int
    elapsed: float  # seconds, final attempt only
    attempts: int
    etag: str | None = None


def parse_retry_after(value: str | None) -> float | None:
    """Seconds to wait, from a `Retry-After` header (delta-seconds or an HTTP-date)."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    if when.tzinfo is None:  # HTTP-dates are GMT
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _json_or_none(body: bytes) -> Any:
    try:
        return json.loads(body) if body else None
    except ValueError:
        return None


class Fetcher:
    """A shared client. Requests take a host slot, wait for that host's pacing gap, then
    take a global slot, so requests queued behind a busy host never hold a global one."""

    def __init__(
        self,
        *,
        max_concurrency: int = 8,
        per_host: int = 2,
        host_limits: dict[str, int] | None = None,
        min_gap: float = 0.25,
        host_gaps: dict[str, float] | None = None,
        max_attempts: int = 4,
        max_retry_after: float = 60.0,
        deadline: float | None = None,
        backoff_base: float = 1.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """`deadline` is a `time.monotonic()` value; `transport` lets tests mock HTTP."""
        self.per_host = per_host
        self.host_limits = host_limits or {}
        self.min_gap = min_gap
        self.host_gaps = host_gaps or {}
        self.max_attempts = max_attempts
        self.max_retry_after = max_retry_after
        self.deadline = deadline
        self.backoff_base = backoff_base
        self.stats: defaultdict[str, HostStats] = defaultdict(HostStats)
        self._global = asyncio.Semaphore(max_concurrency)
        self._host_sems: dict[str, asyncio.Semaphore] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last_start: dict[str, float] = {}
        self.client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=60.0),
            limits=httpx.Limits(
                max_connections=max_concurrency,
                max_keepalive_connections=max_concurrency,
            ),
            follow_redirects=True,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.client.aclose()

    def _host_sem(self, host: str) -> asyncio.Semaphore:
        if host not in self._host_sems:
            limit = self.host_limits.get(host, self.per_host)
            self._host_sems[host] = asyncio.Semaphore(limit)
        return self._host_sems[host]

    async def _pace(self, host: str) -> None:
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        gap = self.host_gaps.get(host, self.min_gap)
        async with lock:
            wait = self._host_last_start.get(host, 0.0) + gap - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._host_last_start[host] = time.monotonic()

    def _check_deadline(self, extra: float = 0.0) -> None:
        if self.deadline is not None and time.monotonic() + extra > self.deadline:
            raise DeadlineExceeded("run time budget exhausted")

    async def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        """GET and parse JSON. Retries network errors, 408, 425, 429 and 5xx; returns
        any other status (304, 404, ...) to the caller instead of raising."""
        host = urlsplit(url).hostname or ""
        stats = self.stats[host]
        attempt = 0
        while True:
            attempt += 1
            self._check_deadline()
            response: httpx.Response | None = None
            error = ""
            async with self._host_sem(host):
                await self._pace(host)
                async with self._global:
                    self._check_deadline()
                    stats.requests += 1
                    if attempt > 1:
                        stats.retries += 1
                    started = time.monotonic()
                    try:
                        response = await self.client.get(
                            url, params=params, headers=headers
                        )
                    except httpx.RequestError as e:  # timeouts, resets, bad gzip, ...
                        error = f"{type(e).__name__}: {e}"
                        stats.statuses[type(e).__name__] += 1
                    elapsed = time.monotonic() - started
                    stats.request_seconds += elapsed
            retry_after: float | None = None
            status: int | None = None
            if response is not None:
                status = response.status_code
                stats.statuses[str(status)] += 1
                stats.bytes_wire += response.num_bytes_downloaded
                stats.bytes_decoded += len(response.content)
                if status not in RETRYABLE_STATUSES:
                    return Response(
                        url=str(response.url),
                        status=status,
                        data=_json_or_none(response.content),
                        bytes_wire=response.num_bytes_downloaded,
                        bytes_decoded=len(response.content),
                        elapsed=elapsed,
                        attempts=attempt,
                        etag=response.headers.get("ETag"),
                    )
                retry_after = parse_retry_after(response.headers.get("Retry-After"))
                error = f"HTTP {status}"
            if attempt >= self.max_attempts:
                raise FetchError(
                    url, f"gave up after {attempt} attempts: {error}", status
                )
            if retry_after is not None and retry_after > self.max_retry_after:
                raise FetchError(
                    url, f"Retry-After {retry_after:.0f}s is too long", status
                )
            if retry_after is not None:
                delay = max(retry_after, 0.5)
            else:  # exponential, jittered x0.5-1.5, capped
                base = self.backoff_base * 2.0 ** (attempt - 1)
                delay = min(30.0, base) * (0.5 + random.random())
            self._check_deadline(delay)
            await asyncio.sleep(delay)

    def totals(self) -> dict[str, Any]:
        """Request, retry and byte counts, overall and per host."""
        out: dict[str, Any] = {
            "requests": sum(s.requests for s in self.stats.values()),
            "retries": sum(s.retries for s in self.stats.values()),
            "bytes_wire": sum(s.bytes_wire for s in self.stats.values()),
            "bytes_decoded": sum(s.bytes_decoded for s in self.stats.values()),
        }
        out["by_host"] = {
            host: {
                "requests": s.requests,
                "retries": s.retries,
                "statuses": dict(s.statuses),
                "bytes_wire": s.bytes_wire,
                "mean_request_seconds": round(s.request_seconds / s.requests, 3)
                if s.requests
                else None,
            }
            for host, s in sorted(self.stats.items())
        }
        return out
