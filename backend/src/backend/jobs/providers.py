"""One fetcher per source. Each returns a `BoardFetch`; `complete` is True only when the
whole board listing came back (every page answered, counts consistent), which is what
allows closing jobs that disappeared from it."""

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from .http import DeadlineExceeded, Fetcher, FetchError, Response
from .normalize import canonical_json, content_hash

DETAIL_PROVENANCE_VERSION = 1


def _list_signature(item: Any) -> str:
    return hashlib.sha256(canonical_json(item).encode()).hexdigest()


def _detail_payload(item: Any, previous: Any) -> dict[str, Any]:
    """Keep old detail data and its *original* provenance until a refresh succeeds.

    Legacy payloads lack proof of which list revision their detail belongs to, so
    they are deliberately refreshed once, without discarding existing descriptions.
    """
    return {
        "list": item,
        "detail": previous.get("detail"),
        "detail_provenance": previous.get("detail_provenance"),
    }


Item = tuple[str, Any, str]  # (source_id, raw payload, content_hash)


@dataclass
class BoardFetch:
    provider: str
    board: str
    company: str
    unit: str = ""  # e.g. a Himalayas search slice
    items: list[Item] = field(default_factory=list)
    complete: bool = False
    status: str = "failed"  # ok | partial | failed
    not_modified: bool = False  # every page answered 304: same listing as last time
    validators: dict[str, str] = field(default_factory=dict)  # url -> ETag of 200s
    http_status: int | None = None
    pages: int = 0
    requests: int = 0
    bytes_wire: int = 0
    reported_total: int | None = None
    error: str | None = None
    started: float = field(default_factory=time.monotonic)
    detail_calls: int = 0
    detail_reused: int = 0
    detail_deferred: int = 0
    detail_seconds: float = 0.0

    def account(self, r: Response) -> None:
        self.requests += r.attempts
        self.bytes_wire += r.bytes_wire
        self.http_status = r.status

    def fail(self, error: BaseException | str) -> BoardFetch:
        self.status, self.complete = "failed", False
        self.error = (
            error if isinstance(error, str) else f"{type(error).__name__}: {error}"
        )
        return self


def _conditional(validators: dict[str, str] | None, url: str) -> dict[str, str] | None:
    etag = (validators or {}).get(url)
    return {"If-None-Match": etag} if etag else None


def _items(jobs: dict[str, Any]) -> list[Item]:
    return [(source_id, raw, content_hash(raw)) for source_id, raw in jobs.items()]


async def _single_page(
    f: Fetcher, bf: BoardFetch, url: str, validators: dict[str, str] | None
) -> Response | None:
    """GET a whole-board endpoint; None when it answered 304 (bf is then complete)."""
    conditional = _conditional(validators, url)
    r = await f.get_json(url, headers=conditional)
    bf.account(r)
    bf.pages = 1
    if r.status == 304:
        if not conditional:
            bf.fail("unexpected 304 without a stored validator")
            return None
        bf.not_modified, bf.complete, bf.status = True, True, "ok"
        return None
    if r.etag:
        bf.validators[url] = r.etag
    return r


async def fetch_greenhouse(
    f: Fetcher, board: dict[str, Any], validators: dict[str, str] | None = None
) -> BoardFetch:
    bf = BoardFetch("greenhouse", board["id"], board["company"])
    url = (
        f"https://boards-api.greenhouse.io/v1/boards/{quote(board['id'])}/jobs"
        "?content=true&pay_transparency=true"
    )
    try:
        r = await _single_page(f, bf, url, validators)
        if r is None:
            return bf
        if (
            r.status != 200
            or not isinstance(r.data, dict)
            or not isinstance(r.data.get("jobs"), list)
        ):
            return bf.fail(
                f"HTTP {r.status}: no jobs array (board removed or renamed?)"
            )
        jobs = r.data["jobs"]
        bf.reported_total = (r.data.get("meta") or {}).get("total")
        bf.items = _items({str(j["id"]): j for j in jobs})
        bf.complete = bf.reported_total in (None, len(jobs)) and len(bf.items) == len(
            jobs
        )
        bf.status = "ok" if bf.complete else "partial"
    except (FetchError, DeadlineExceeded) as e:
        return bf.fail(e)
    return bf


async def fetch_ashby(
    f: Fetcher, board: dict[str, Any], validators: dict[str, str] | None = None
) -> BoardFetch:
    bf = BoardFetch("ashby", board["id"], board["company"])
    url = (
        f"https://api.ashbyhq.com/posting-api/job-board/{quote(board['id'])}"
        "?includeCompensation=true"
    )
    try:
        r = await _single_page(f, bf, url, validators)
        if r is None:
            return bf
        if (
            r.status != 200
            or not isinstance(r.data, dict)
            or not isinstance(r.data.get("jobs"), list)
        ):
            return bf.fail(f"HTTP {r.status}: no jobs array")
        jobs = r.data["jobs"]
        bf.reported_total = len(jobs)
        listed = [j for j in jobs if j.get("isListed") is not False]  # respect unlisted
        bf.items = _items({str(j.get("id") or j.get("jobUrl")): j for j in listed})
        bf.complete = len(bf.items) == len(listed)
        bf.status = "ok" if bf.complete else "partial"
    except (FetchError, DeadlineExceeded) as e:
        return bf.fail(e)
    return bf


async def fetch_lever(
    f: Fetcher,
    board: dict[str, Any],
    validators: dict[str, str] | None = None,
    page_size: int = 1000,
) -> BoardFetch:
    """Lever ignores Accept-Encoding (about 15 KB per job, uncompressed), so a 304 saves
    the most here. With 1,000 per page every seed board is a single page; the 304
    shortcut is only trusted for single-page boards."""
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    bf = BoardFetch("lever", board["id"], board["company"])
    host = board.get("host", "api.lever.co")  # EU employers use api.eu.lever.co
    base = (
        f"https://{host}/v0/postings/{quote(board['id'])}?mode=json&limit={page_size}"
    )
    multi_page = any(
        u.startswith(base) and not u.endswith("&skip=0") for u in validators or {}
    )
    seen: dict[str, Any] = {}
    try:
        skip = 0
        while True:
            url = f"{base}&skip={skip}"
            conditional = (
                _conditional(validators, url) if skip == 0 and not multi_page else None
            )
            r = await f.get_json(url, headers=conditional)
            bf.account(r)
            bf.pages += 1
            if r.status == 304:
                if not conditional or skip != 0:
                    bf.items = _items(seen)
                    return bf.fail(
                        "unexpected 304 for an uncached or multi-page listing"
                    )
                bf.not_modified, bf.complete, bf.status = True, True, "ok"
                return bf
            if r.status != 200 or not isinstance(r.data, list):
                bf.items = _items(seen)
                return bf.fail(f"HTTP {r.status} at skip={skip}")
            if r.etag:
                bf.validators[url] = r.etag
            before = len(seen)
            for job in r.data:
                seen[str(job["id"])] = job
            if len(seen) - before != len(r.data):
                bf.items = _items(seen)
                return bf.fail(
                    f"duplicate posting IDs at skip={skip}; inventory changed during pagination"
                )
            if len(r.data) < page_size:
                break
            skip += page_size
        bf.items = _items(seen)
        bf.reported_total = len(seen)
        bf.complete, bf.status = True, "ok"
        if bf.pages > 1:
            # A first-page 304 cannot prove that subsequent pages are unchanged.
            bf.validators.clear()
    except (FetchError, DeadlineExceeded) as e:
        bf.items = _items(seen)
        return bf.fail(e)
    return bf


async def fetch_smartrecruiters(
    f: Fetcher, board: dict[str, Any], stored: dict[str, Any], detail_cap: int
) -> BoardFetch:
    """List pages (filtered server-side by `countries`), then one detail call per posting
    for its description. A stored detail is reused while the list item is unchanged; at
    most `detail_cap` new detail calls per company per run, newest postings first. The
    rest are stored without a description and filled in by later runs."""
    if detail_cap < 0:
        raise ValueError("detail_cap must be nonnegative")
    bf = BoardFetch("smartrecruiters", board["id"], board["company"])
    base = f"https://api.smartrecruiters.com/v1/companies/{quote(board['id'])}/postings"
    listed: dict[str, Any] = {}
    complete, list_failed = True, False
    try:
        for country in board.get("countries") or [None]:
            offset, total = 0, 0
            while True:
                params: dict[str, Any] = {"limit": 100, "offset": offset}
                if country:
                    params["country"] = country
                r = await f.get_json(base, params=params)
                bf.account(r)
                bf.pages += 1
                if r.status != 200 or not isinstance(r.data, dict):
                    complete = False
                    bf.error = f"HTTP {r.status} at country={country} offset={offset}"
                    break
                total, content = r.data.get("totalFound"), r.data.get("content")
                if (
                    not isinstance(total, int)
                    or total < 0
                    or not isinstance(content, list)
                ):
                    complete = False
                    bf.error = "invalid SmartRecruiters inventory shape"
                    total = 0
                    break
                for item in content:
                    listed[str(item["id"])] = item
                offset += len(content)
                if not content or offset >= total:
                    break
            bf.reported_total = (bf.reported_total or 0) + total
        # offset pagination can skip or repeat postings while the list changes
        complete = complete and len(listed) == bf.reported_total
    except (FetchError, DeadlineExceeded) as e:
        bf.fail(e)
        complete, list_failed = False, True

    raws: dict[str, Any] = {}
    need: list[str] = []
    for source_id, item in listed.items():
        previous = stored.get(source_id) or {}
        provenance = {
            "version": DETAIL_PROVENANCE_VERSION,
            "list_hash": _list_signature(item),
        }
        if previous.get("detail") and previous.get("detail_provenance") == provenance:
            raws[source_id] = previous
            bf.detail_reused += 1
        else:
            need.append(source_id)
    need.sort(key=lambda s: listed[s].get("releasedDate") or "", reverse=True)

    async def detail(source_id: str) -> None:
        pending = _detail_payload(listed[source_id], stored.get(source_id) or {})
        bf.detail_calls += 1
        try:
            r = await f.get_json(f"{base}/{source_id}")
        except (FetchError, DeadlineExceeded) as e:
            raws[source_id] = pending
            bf.detail_deferred += 1
            bf.error = bf.error or f"detail {source_id}: {e}"
            return
        bf.detail_seconds += r.elapsed  # request time, not time queued for a host slot
        bf.requests += r.attempts
        bf.bytes_wire += r.bytes_wire
        ok = (
            r.status == 200
            and isinstance(r.data, dict)
            and str(r.data.get("id")) == source_id
        )
        if ok:
            pending.update(
                detail=r.data,
                detail_provenance={
                    "version": DETAIL_PROVENANCE_VERSION,
                    "list_hash": _list_signature(listed[source_id]),
                },
            )
        else:
            bf.detail_deferred += 1
            bf.error = (
                bf.error
                or f"detail {source_id}: HTTP {r.status}, invalid detail response"
            )
        raws[source_id] = pending

    async with asyncio.TaskGroup() as group:
        for source_id in need[:detail_cap]:
            group.create_task(detail(source_id))
    for source_id in need[
        detail_cap:
    ]:  # an older detail if we have one, else list only
        raws[source_id] = _detail_payload(
            listed[source_id], stored.get(source_id) or {}
        )
        bf.detail_deferred += 1
    bf.items = _items(raws)
    if not list_failed:
        bf.complete = complete
        bf.status = "ok" if complete and not bf.detail_deferred else "partial"
    return bf


async def fetch_himalayas(
    f: Fetcher, query: str, country: str, pages: int
) -> BoardFetch:
    """One search slice. Search results are never a complete inventory, so a job missing
    from them must not be closed: `complete` stays False."""
    if pages <= 0:
        raise ValueError("pages must be positive")
    bf = BoardFetch("himalayas", "*", "Himalayas", unit=f"q={query} country={country}")
    try:
        for page in range(1, pages + 1):
            r = await f.get_json(
                "https://himalayas.app/jobs/api/search",
                params={
                    "q": query,
                    "country": country,
                    "exclude_worldwide": "true",
                    "sort": "recent",
                    "page": page,
                },
            )
            bf.account(r)
            bf.pages += 1
            if r.status != 200 or not isinstance(r.data, dict):
                return bf.fail(f"HTTP {r.status} at page {page}")
            jobs = r.data.get("jobs")
            if not isinstance(jobs, list):
                return bf.fail(f"no jobs array at page {page}")
            bf.reported_total = r.data.get("totalCount")
            bf.items += [(job["guid"], job, content_hash(job)) for job in jobs]
            if len(jobs) < (r.data.get("limit") or 20):
                break
        bf.status = "ok"
    except (FetchError, DeadlineExceeded) as e:
        return bf.fail(e)
    return bf
