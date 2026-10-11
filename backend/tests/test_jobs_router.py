"""GET /jobs and /jobs/stats against a temporary jobs.db."""

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.jobs import normalize, store
from backend.jobs.router import router

FIXTURES = Path(__file__).parent / "fixtures" / "jobs"
NOW = "2026-10-11T12:00:00+00:00"


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def client(data_dir: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def seed(db: Path) -> None:
    """Greenhouse fixture jobs (all US/Canada), plus a copy moved to London."""
    jobs = json.loads((FIXTURES / "greenhouse.json").read_text())["jobs"]
    london = jobs[0] | {
        "id": 1,
        "location": {"name": "London, United Kingdom"},
        "offices": [],
    }
    board = {"id": "acme", "company": "Acme"}

    def build(raw: Any, chash: str) -> dict[str, Any]:
        return normalize.to_row(
            "greenhouse", "acme", raw, normalize.greenhouse(raw, board), chash
        )

    conn = store.connect(db)
    run = store.start_run(conn, NOW, "test")
    items = [(str(j["id"]), j, normalize.content_hash(j)) for j in [*jobs, london]]
    store.apply_board(conn, "greenhouse", "acme", items, build, NOW, complete=True)
    store.finish_run(conn, run, {"status": "ok", "finished_at": NOW})
    conn.close()


def test_before_the_first_sync(client: TestClient) -> None:
    response = client.get("/jobs", params={"q": "python"})
    assert response.status_code == 200
    assert response.json() == {"synced": False, "total": 0, "jobs": []}
    stats = client.get("/jobs/stats").json()
    assert stats == {
        "synced": False,
        "open_jobs": 0,
        "by_provider": {},
        "by_geo": {},
        "last_run": None,
        "last_success_at": None,
    }


def test_search(client: TestClient, data_dir: Path) -> None:
    seed(data_dir / "jobs.db")
    data = client.get("/jobs", params={"q": "aircover"}).json()
    assert data["synced"] is True
    assert data["total"] == 2  # the London copy is outside the default geo
    titles = [job["title"] for job in data["jobs"]]
    assert titles == [  # both titles say AirCover; BM25 favours the shorter one
        "Quality Manager, AirCover",
        "Staff Software Engineer, Payments (AirCover Insurance Platform)",
    ]
    first = data["jobs"][1]
    assert first["url"] == "https://careers.airbnb.com/positions/8259526?gh_jid=8259526"
    assert (first["salary_min"], first["salary_currency"]) == (212000, "USD")
    assert "description_text" not in first
    assert "description" not in first

    assert (
        client.get("/jobs", params={"q": "aircover", "geo": "any"}).json()["total"] == 3
    )
    london = client.get("/jobs", params={"q": "aircover", "geo": "other"}).json()[
        "jobs"
    ]
    assert [job["locations"] for job in london] == ["London, United Kingdom"]


def test_search_syntax(client: TestClient, data_dir: Path) -> None:
    seed(data_dir / "jobs.db")
    assert client.get("/jobs", params={"q": "aircover AND canada"}).json()["total"] == 1
    assert (
        client.get("/jobs", params={"q": 'title:"quality manager"'}).json()["total"]
        == 1
    )
    for broken in [
        'c++ "unbalanced',
        "AND",
        "(",
        '"',
    ]:  # invalid FTS5: words are retried as phrases
        response = client.get("/jobs", params={"q": broken})
        assert response.status_code == 200
    assert client.get("/jobs", params={"q": "stripe("}).json()["total"] == 1


def test_listing_without_query(client: TestClient, data_dir: Path) -> None:
    seed(data_dir / "jobs.db")
    data = client.get("/jobs", params={"limit": 2}).json()
    assert data["total"] == 3
    assert len(data["jobs"]) == 2
    assert client.get("/jobs", params={"limit": 0}).status_code == 422


def test_closed_jobs_are_hidden(client: TestClient, data_dir: Path) -> None:
    seed(data_dir / "jobs.db")
    conn = store.connect(data_dir / "jobs.db")
    conn.execute("UPDATE jobs SET closed_at = ? WHERE source_id = '8138044'", (NOW,))
    conn.close()
    assert client.get("/jobs", params={"q": "stripe"}).json()["total"] == 0
    assert client.get("/jobs/stats").json()["open_jobs"] == 3


def test_stats(client: TestClient, data_dir: Path) -> None:
    seed(data_dir / "jobs.db")
    stats = client.get("/jobs/stats").json()
    assert stats["synced"] is True
    assert stats["open_jobs"] == 4
    assert stats["by_provider"] == {"greenhouse": 4}
    assert stats["by_geo"] == {"us_ca": 3, "other": 1}
    assert stats["last_run"] == {"status": "ok", "started_at": NOW, "finished_at": NOW}
    assert stats["last_success_at"] == NOW
