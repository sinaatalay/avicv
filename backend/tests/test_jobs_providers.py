"""Provider inventory and detail provenance regressions; all requests are mocked."""

import json
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from backend.jobs.http import Fetcher
from backend.jobs.providers import fetch_greenhouse, fetch_lever, fetch_smartrecruiters

pytestmark = pytest.mark.anyio
FIXTURES = Path(__file__).parent / "fixtures" / "jobs"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize("failed_refresh", [False, True])
async def test_changed_detail_remains_pending_until_success(
    failed_refresh: bool,
) -> None:
    item = json.loads((FIXTURES / "smartrecruiters_list.json").read_text())["content"][
        0
    ]
    source_id = item["id"]
    detail = json.loads((FIXTURES / "smartrecruiters_detail.json").read_text())[
        source_id
    ]
    board = {"id": "Acme", "company": "Acme"}
    calls = 0
    fail = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path.endswith("/postings"):
            return httpx.Response(200, json={"content": [item], "totalFound": 1})
        calls += 1
        return httpx.Response(404) if fail else httpx.Response(200, json=detail)

    async with Fetcher(transport=httpx.MockTransport(handler), min_gap=0) as f:
        initial = await fetch_smartrecruiters(f, board, {}, 1)
        original = initial.items[0][1]
        item = item | {"name": "Changed title"}
        fail = failed_refresh
        deferred = await fetch_smartrecruiters(
            f, board, {source_id: original}, 1 if fail else 0
        )
        pending = deferred.items[0][1]
        assert deferred.status == "partial"
        assert pending["detail"] == detail
        assert pending["detail_provenance"] == original["detail_provenance"]
        fail = False
        updated = await fetch_smartrecruiters(f, board, {source_id: pending}, 1)
        assert updated.detail_calls == 1
        assert updated.detail_reused == 0
        assert updated.status == "ok"
        assert updated.items[0][1]["detail_provenance"] != original["detail_provenance"]
        reused = await fetch_smartrecruiters(
            f, board, {source_id: updated.items[0][1]}, 1
        )
        assert (reused.detail_calls, reused.detail_reused) == (0, 1)
    assert calls == (3 if failed_refresh else 2)


async def test_legacy_details_are_refetched_even_with_equal_list_item() -> None:
    item = {"id": "1", "name": "Engineer"}
    old = {"list": item, "detail": {"id": "1", "applyUrl": "old"}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"totalFound": 1, "content": [item]}
            if request.url.path.endswith("postings")
            else {"id": "1", "applyUrl": "new"},
        )

    async with Fetcher(transport=httpx.MockTransport(handler), min_gap=0) as f:
        result = await fetch_smartrecruiters(
            f, {"id": "acme", "company": "Acme"}, {"1": old}, 1
        )
    assert result.detail_calls == 1
    assert result.items[0][1]["detail"]["applyUrl"] == "new"


async def test_multi_page_lever_never_keeps_first_page_validator() -> None:
    observed: list[str | None] = []
    pages = [[{"id": "1"}, {"id": "2"}], [{"id": "3"}]]

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request.headers.get("If-None-Match"))
        page = int(request.url.params["skip"]) // 2
        return httpx.Response(200, json=pages[page], headers={"ETag": f"page{page}"})

    url = "https://api.lever.co/v0/postings/acme?mode=json&limit=2&skip=0"
    async with Fetcher(transport=httpx.MockTransport(handler), min_gap=0) as f:
        result = await fetch_lever(
            f, {"id": "acme", "company": "Acme"}, {url: "old-single-page"}, page_size=2
        )
    assert observed == ["old-single-page", None]
    assert result.complete and len(result.items) == 3
    assert result.validators == {}


async def test_lever_repeated_page_does_not_look_like_complete_inventory() -> None:
    async with Fetcher(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=[{"id": "1"}, {"id": "2"}])
        ),
        min_gap=0,
    ) as f:
        result = await fetch_lever(f, {"id": "acme", "company": "Acme"}, page_size=2)
    assert result.status == "failed" and not result.complete
    assert result.pages == 2


@pytest.mark.parametrize("provider", [fetch_greenhouse, fetch_lever])
async def test_unsolicited_304_is_not_trusted(provider: Any) -> None:
    async with Fetcher(
        transport=httpx.MockTransport(lambda request: httpx.Response(304)), min_gap=0
    ) as f:
        result = await provider(f, {"id": "acme", "company": "Acme"})
    assert result.status == "failed" and not result.not_modified
