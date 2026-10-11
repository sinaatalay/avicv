import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db import connect, connect_app
from backend.main import app
from backend.matching import jev
from backend.resumes import pipeline, queue

YAML = "cv:\n  name: Test Candidate\n  headline: Backend Engineer\n  location: Toronto, Canada\n  sections:\n    Skills:\n      - label: Languages\n        details: Python, Go\n"


def seed_resume(
    settings: Settings,
    resume_id: str = "resume",
    *,
    yaml: str = YAML,
    queued: bool = True,
) -> None:
    conn = connect_app(settings.app_db)
    with conn:
        conn.execute(
            """
            INSERT INTO resumes (id, created_at, pdf_sha256, status, rendercv_yaml)
            VALUES (?, '2026-10-11', 'hash', ?, ?)
        """,
            (resume_id, "queued" if queued else "done", yaml),
        )
        if queued:
            queue.enqueue(conn, resume_id)
    conn.close()


def role(index: int, location: float = 0.9) -> dict[str, Any]:
    return {
        "id": index,
        "provider": "test",
        "board": "board",
        "source_id": str(index),
        "content_hash": f"hash-{index}",
        "title": f"Engineer {index}",
        "company": "Company",
        "locations": ["Toronto"],
        "description_text": "Python backend engineer",
        "test_location": location,
    }


def score(job: dict[str, Any]) -> jev.JobScore:
    return jev.JobScore(
        job["id"],
        job["content_hash"],
        0.9 - job["id"] / 1000,
        3.5,
        0.8,
        0.9,
        job.get("test_location", 0.9),
        "matches",
        1000,
    )


@pytest.fixture
def fake_models(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []

    @asynccontextmanager
    async def client(settings):
        yield object()

    async def scoring(client, candidate, jobs, *, concurrency):
        for job in jobs:
            calls.append(job["id"])
            yield job, score(job)

    monkeypatch.setattr(jev, "make_client", client)
    monkeypatch.setattr(jev, "score_jobs", scoring)
    return calls


def run_next(settings: Settings) -> None:
    claim = queue.claim_next(settings)
    assert claim is not None
    asyncio.run(queue.run_claim(settings, claim))


def test_claims_are_globally_atomic_and_expired_worker_is_fenced(
    settings: Settings,
) -> None:
    seed_resume(settings, "one")
    seed_resume(settings, "two")
    barrier = Barrier(2)

    def claim():
        barrier.wait()
        return queue.claim_next(settings)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim) for _ in range(2)]
        claims = [future.result() for future in futures]
    live = next(item for item in claims if item is not None)
    assert sum(item is not None for item in claims) == 1
    assert queue.claim_next(settings) is None
    conn = connect_app(settings.app_db)
    conn.execute(
        "UPDATE resume_queue SET lease_until = ? WHERE owner = ?",
        (time.time() - 1, live.owner),
    )
    conn.commit()
    successor = queue.claim_next(settings)
    assert successor is not None and successor.owner != live.owner
    with pytest.raises(queue.LeaseLost):
        queue.fence(conn, live)
    conn.close()


def test_partial_failures_retry_only_failed_roles(
    settings: Settings, jobs_db: Path, fake_models, monkeypatch
) -> None:
    seed_resume(settings)
    attempts: list[int] = []

    async def scoring(client, candidate, jobs, *, concurrency):
        for index, job in enumerate(jobs):
            attempts.append(job["id"])
            yield (
                job,
                RuntimeError("temporary provider failure")
                if len(attempts) == 2
                else score(job),
            )

    monkeypatch.setattr(jev, "score_jobs", scoring)
    run_next(settings)
    client = TestClient(app)  # no lifespan: advance durable worker explicitly
    first = client.get("/resumes/resume").json()
    assert first["status"] == "partial"
    assert first["progress"] == {
        "done": 2,
        "total": 3,
        "succeeded": 2,
        "failed": 1,
        "attempted": 3,
        "pending": 0,
    }
    failed_id = attempts[1]
    assert client.post("/resumes/resume/retry").status_code == 202
    run_next(settings)
    final = client.get("/resumes/resume").json()
    assert final["status"] == "done"
    assert attempts == [attempts[0], failed_id, attempts[2], failed_id]
    assert final["progress"]["succeeded"] == 3
    assert final["progress"]["attempted"] == 3
    assert final["cost_usd"] == pytest.approx(3000 * jev.PRICE_PER_MTOK / 1e6)


def test_shutdown_resumes_without_repeating_successful_work(
    settings: Settings, jobs_db: Path, fake_models, monkeypatch
) -> None:
    seed_resume(settings)
    calls: list[int] = []

    async def scenario():
        saved_first = asyncio.Event()
        never = asyncio.Event()

        async def interrupted(client, candidate, jobs, *, concurrency):
            for job in jobs:
                calls.append(job["id"])
                yield job, score(job)
                saved_first.set()
                await never.wait()

        monkeypatch.setattr(jev, "score_jobs", interrupted)
        first_claim = queue.claim_next(settings)
        assert first_claim is not None
        running = asyncio.create_task(queue.run_claim(settings, first_claim))
        await saved_first.wait()
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        conn = connect_app(settings.app_db)
        assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1
        assert conn.execute("SELECT state FROM resume_queue").fetchone()[0] == "pending"
        conn.close()

        async def finish(client, candidate, jobs, *, concurrency):
            for job in jobs:
                calls.append(job["id"])
                yield job, score(job)

        monkeypatch.setattr(jev, "score_jobs", finish)
        await queue.run_claim(settings, next_claim(settings))

    asyncio.run(scenario())
    assert len(calls) == len(set(calls)) == 3
    assert TestClient(app).get("/resumes/resume").json()["status"] == "done"


def test_empty_catalog_waits_and_continues_after_ingestion(
    settings: Settings, jobs_db: Path, fake_models
) -> None:
    catalog = jobs_db.with_suffix(".saved")
    jobs_db.rename(catalog)
    seed_resume(settings)
    run_next(settings)
    client = TestClient(app)
    assert client.get("/resumes/resume").json()["status"] == "waiting_for_jobs"
    assert fake_models == []
    assert queue.claim_next(settings) is None  # respects backoff
    catalog.rename(jobs_db)
    conn = connect_app(settings.app_db)
    conn.execute("UPDATE resume_queue SET available_at = 0")
    conn.commit()
    conn.close()
    run_next(settings)
    assert client.get("/resumes/resume").json()["status"] == "done"
    assert len(fake_models) == 3


def test_filters_apply_before_pagination_and_unknown_location_keeps_results(
    settings: Settings,
) -> None:
    seed_resume(settings, queued=False)
    conn = connect_app(settings.app_db)
    for index in range(1, 121):
        job = role(index, 0.9 if index > 100 else 0.1)
        pipeline.save_score(conn, "resume", 0, 0, job, score(job))
    conn.commit()
    client = TestClient(app)
    page = client.get(
        "/resumes/resume/match-results?nearby=true&limit=5&offset=10"
    ).json()
    assert page["unfiltered_total"] == 120
    assert page["nearby_total"] == page["total"] == 20
    assert [item["job_id"] for item in page["items"]] == [111, 112, 113, 114, 115]
    assert page["has_more"] is True
    conn.execute(
        "UPDATE resumes SET rendercv_yaml = ?",
        (YAML.replace("  location: Toronto, Canada\n", ""),),
    )
    conn.commit()
    conn.close()
    unknown = client.get("/resumes/resume/match-results?nearby=true&limit=5").json()
    assert unknown["candidate_location_known"] is False
    assert unknown["location_source"] == "unknown"
    assert unknown["nearby_total"] is None and unknown["total"] == 120
    assert all(item["location"] is None for item in unknown["items"])


def test_catalog_changes_never_replace_original_job_and_closed_filter_counts(
    settings: Settings, jobs_db: Path
) -> None:
    seed_resume(settings, queued=False)
    conn = connect_app(settings.app_db)
    catalog = connect(jobs_db)
    original = dict(catalog.execute("SELECT * FROM jobs WHERE id = 1").fetchone()) | {
        "locations": ["Toronto"]
    }
    pipeline.save_score(conn, "resume", 0, 0, original, score(original))
    conn.commit()
    conn.close()
    client = TestClient(app)
    catalog.execute(
        "UPDATE jobs SET title = 'Accountant', content_hash = 'new' WHERE id = 1"
    )
    catalog.commit()
    changed = client.get("/resumes/resume/match-results").json()["items"][0]
    assert changed["title"] == "Backend Engineer, Payments"
    assert changed["catalog_status"] == "changed"
    catalog.execute("UPDATE jobs SET closed_at = '2026-10-12' WHERE id = 1")
    catalog.commit()
    hidden = client.get("/resumes/resume/match-results").json()
    assert hidden["total"] == 0 and hidden["closed_total"] == 1
    assert (
        client.get("/resumes/resume/match-results?include_closed=true").json()["items"][
            0
        ]["catalog_status"]
        == "closed"
    )
    # A rebuilt database can give numeric ID 1 to an unrelated source.
    catalog.execute(
        "UPDATE jobs SET source_id = 'unrelated', closed_at = NULL WHERE id = 1"
    )
    catalog.commit()
    catalog.close()
    rebuilt = client.get("/resumes/resume/match-results").json()["items"][0]
    assert rebuilt["title"] == "Backend Engineer, Payments"
    assert rebuilt["catalog_status"] == "missing"
    jobs_db.unlink()
    missing = client.get("/resumes/resume/match-results").json()["items"][0]
    assert missing["title"] == "Backend Engineer, Payments"
    assert missing["score"] == changed["score"]


def test_upload_is_atomic_and_delete_removes_pdf_queue_and_results(
    settings: Settings, make_pdf, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "queue_max_pending", 1)
    client = TestClient(app)
    pdf = make_pdf()
    response = client.post(
        "/resumes", files={"file": ("cv.pdf", pdf, "application/pdf")}
    )
    assert response.status_code == 202
    resume_id = response.json()["id"]
    conn = connect_app(settings.app_db)
    assert conn.execute("SELECT upload_pdf FROM resumes").fetchone()[0] == pdf
    assert conn.execute("SELECT state FROM resume_queue").fetchone()[0] == "pending"
    assert (
        client.post(
            "/resumes", files={"file": ("another.pdf", pdf, "application/pdf")}
        ).status_code
        == 429
    )
    assert conn.execute("SELECT count(*) FROM resumes").fetchone()[0] == 1
    assert client.delete(f"/resumes/{resume_id}").status_code == 204
    assert conn.execute("SELECT count(*) FROM resumes").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM resume_queue").fetchone()[0] == 0
    assert client.get(f"/resumes/{resume_id}").status_code == 404
    conn.close()


def test_rematch_keeps_old_generation_until_plan_and_reuses_only_unchanged_scores(
    settings: Settings, jobs_db: Path, fake_models
) -> None:
    seed_resume(settings)
    run_next(settings)
    assert len(fake_models) == 3
    client = TestClient(app)
    previous = client.get("/resumes/resume/match-results").json()
    assert client.post("/resumes/resume/rematch").status_code == 202
    assert (
        client.get("/resumes/resume/match-results").json()["items"] == previous["items"]
    )
    run_next(settings)
    assert len(fake_models) == 3
    assert (
        client.post(
            "/resumes/resume/rematch", json={"location": "Vancouver, Canada"}
        ).status_code
        == 202
    )
    run_next(settings)
    assert len(fake_models) == 6
    conn = connect_app(settings.app_db)
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 6
    assert (
        conn.execute(
            "SELECT COUNT(DISTINCT candidate_revision) FROM matches"
        ).fetchone()[0]
        == 2
    )
    conn.close()


def test_default_location_is_exposed_only_for_new_scoring(
    settings: Settings, jobs_db: Path, fake_models
) -> None:
    seed_resume(settings, yaml=YAML.replace("  location: Toronto, Canada\n", ""))
    run_next(settings)
    result = TestClient(app).get("/resumes/resume/match-results").json()
    assert result["effective_location"] == "United States"
    assert result["location_source"] == "default"
    assert result["candidate_location_known"] is True
    assert TestClient(app).get("/resumes/resume").json()["cv"]["location"] is None


def test_scoring_identity_covers_model_and_group_locations() -> None:
    from backend.resumes.storage import job_key

    first = role(1) | {"scoring_model": "one", "questions_version": "2"}
    assert job_key(first) != job_key(first | {"scoring_model": "two"})
    assert job_key(first) != job_key(first | {"locations": ["Toronto", "Remote US"]})
    assert job_key(first) != job_key(first | {"questions_version": "3"})
    assert first["content_hash"] == "hash-1"


def test_deploy_during_pending_plan_rebuilds_instead_of_false_completion(
    settings: Settings, jobs_db: Path, fake_models, monkeypatch
) -> None:
    seed_resume(settings)

    async def scenario():
        saved = asyncio.Event()
        never = asyncio.Event()

        async def interrupted(client, candidate, jobs, *, concurrency):
            yield jobs[0], score(jobs[0])
            saved.set()
            await never.wait()

        monkeypatch.setattr(jev, "score_jobs", interrupted)
        run = asyncio.create_task(queue.run_claim(settings, next_claim(settings)))
        await saved.wait()
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)
        monkeypatch.setattr(jev, "QUESTIONS_VERSION", "next-question-version")
        calls = []

        async def finish(client, candidate, jobs, *, concurrency):
            for job in jobs:
                calls.append(job["id"])
                yield job, score(job)

        monkeypatch.setattr(jev, "score_jobs", finish)
        await queue.run_claim(settings, next_claim(settings))
        assert len(calls) == 3

    asyncio.run(scenario())
    data = TestClient(app).get("/resumes/resume").json()
    assert data["status"] == "done"
    assert data["progress"]["pending"] == 0
    assert data["progress"]["succeeded"] == 3
    conn = connect_app(settings.app_db)
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 4
    conn.close()


def test_failed_rematch_preserves_previous_results_and_location_context(
    settings: Settings,
    jobs_db: Path,
    fake_models,
    monkeypatch,
) -> None:
    seed_resume(settings)
    run_next(settings)
    client = TestClient(app)
    previous = client.get("/resumes/resume/match-results").json()

    async def fail_every_job(client, candidate, jobs, *, concurrency):
        for job in jobs:
            yield job, RuntimeError("provider unavailable")

    monkeypatch.setattr(jev, "score_jobs", fail_every_job)
    assert (
        client.post(
            "/resumes/resume/rematch", json={"location": "Vancouver, Canada"}
        ).status_code
        == 202
    )
    run_next(settings)
    current = client.get("/resumes/resume/match-results").json()
    assert current["items"] == previous["items"]
    assert current["effective_location"] == "Toronto, Canada"
    assert current["results_are_previous"] is True
    resume = client.get("/resumes/resume").json()
    assert resume["status"] == "failed"
    assert resume["progress"]["failed"] == 3
    assert resume["progress"]["succeeded"] == 0
    assert resume["search"]["effective_location"] == "Vancouver, Canada"


def test_incomplete_scoring_stream_cannot_report_done(
    settings: Settings,
    jobs_db: Path,
    fake_models,
    monkeypatch,
) -> None:
    seed_resume(settings)

    async def incomplete(client, candidate, jobs, *, concurrency):
        yield jobs[0], score(jobs[0])

    monkeypatch.setattr(jev, "score_jobs", incomplete)
    run_next(settings)
    result = TestClient(app).get("/resumes/resume").json()
    assert result["status"] == "failed"
    assert result["progress"]["succeeded"] == 1
    assert result["progress"]["pending"] == 2


def test_legacy_results_are_not_marked_previous_until_new_work_requested(
    settings: Settings,
) -> None:
    seed_resume(settings, queued=False)
    conn = connect_app(settings.app_db)
    job = role(1)
    pipeline.save_score(conn, "resume", 0, 0, job, score(job))
    conn.commit()
    conn.close()
    client = TestClient(app)
    assert client.get("/resumes/resume").json()["results_are_previous"] is False
    assert (
        client.get("/resumes/resume/match-results").json()["results_are_previous"]
        is False
    )
    assert client.post("/resumes/resume/rematch").status_code == 202
    assert (
        client.get("/resumes/resume/match-results").json()["results_are_previous"]
        is True
    )


def next_claim(settings: Settings) -> queue.Claim:
    claim = queue.claim_next(settings)
    assert claim is not None
    return claim


def test_match_categories_use_strict_threshold_and_preserve_legacy_list(
    settings: Settings,
) -> None:
    seed_resume(settings, queued=False)
    conn = connect_app(settings.app_db)
    for index, value in enumerate([0.8000001, 0.8, 0.7999999], start=1):
        job = role(index)
        pipeline.save_score(conn, "resume", 0, 0, job, replace(score(job), score=value))
    conn.commit()
    conn.close()
    client = TestClient(app)
    good = client.get("/resumes/resume/match-results?category=good").json()
    other = client.get("/resumes/resume/match-results?category=other").json()
    all_results = client.get("/resumes/resume/match-results").json()
    assert good["good_total"] == other["good_total"] == all_results["good_total"] == 1
    assert (
        good["other_total"] == other["other_total"] == all_results["other_total"] == 2
    )
    assert [item["job_id"] for item in good["items"]] == [1]
    assert [item["job_id"] for item in other["items"]] == [2, 3]
    assert good["total"] == 1 and other["total"] == 2 and all_results["total"] == 3
    assert len(client.get("/resumes/resume/matches").json()) == 3
    assert [
        item["job_id"]
        for item in client.get("/resumes/resume/matches?category=good").json()
    ] == [1]
    assert (
        client.get("/resumes/resume/match-results?category=invalid").status_code == 422
    )


def test_category_filter_and_counts_cover_results_beyond_first_page(
    settings: Settings,
) -> None:
    seed_resume(settings, queued=False)
    conn = connect_app(settings.app_db)
    for index in range(1, 151):
        job = role(index)
        value = 0.9 if index <= 120 else 0.4
        pipeline.save_score(conn, "resume", 0, 0, job, replace(score(job), score=value))
    conn.commit()
    conn.close()
    client = TestClient(app)
    other = client.get("/resumes/resume/match-results?category=other").json()
    assert other["good_total"] == 120 and other["other_total"] == other["total"] == 30
    assert {item["job_id"] for item in other["items"]} == set(range(121, 151))
    good_page = client.get(
        "/resumes/resume/match-results?category=good&offset=115&limit=5"
    ).json()
    assert good_page["good_total"] == good_page["total"] == 120
    assert good_page["other_total"] == 30
    assert len(good_page["items"]) == 5 and good_page["has_more"] is False
    empty_page = client.get(
        "/resumes/resume/match-results?category=other&offset=30"
    ).json()
    assert empty_page["items"] == [] and empty_page["has_more"] is False
    assert empty_page["total"] == 30 and empty_page["good_total"] == 120


def test_category_counts_respect_location_and_availability_filters(
    settings: Settings, jobs_db: Path
) -> None:
    seed_resume(settings, queued=False)
    conn = connect_app(settings.app_db)
    catalog = connect(jobs_db)
    for index, value, location in [
        (1, 0.9, 0.9),
        (2, 0.8, 0.9),
        (3, 0.95, 0.1),
        (4, 0.3, 0.9),
    ]:
        job = dict(
            catalog.execute("SELECT * FROM jobs WHERE id = ?", (index,)).fetchone()
        ) | {"locations": ["Toronto"]}
        pipeline.save_score(
            conn,
            "resume",
            0,
            0,
            job,
            replace(score(job), score=value, location=location),
        )
    conn.commit()
    conn.close()
    catalog.execute("UPDATE jobs SET closed_at = '2026-10-12' WHERE id = 4")
    catalog.commit()
    catalog.close()
    client = TestClient(app)
    nearby = client.get(
        "/resumes/resume/match-results?nearby=true&category=good"
    ).json()
    assert nearby["good_total"] == nearby["other_total"] == nearby["total"] == 1
    assert nearby["unfiltered_total"] == 3 and nearby["nearby_total"] == 2
    assert nearby["closed_total"] == 1
    assert [item["job_id"] for item in nearby["items"]] == [1]
    including_closed = client.get(
        "/resumes/resume/match-results?nearby=true&category=other&include_closed=true"
    ).json()
    assert including_closed["good_total"] == 1
    assert including_closed["other_total"] == including_closed["total"] == 2
    assert (
        including_closed["good_total"] + including_closed["other_total"]
        == including_closed["nearby_total"]
        == 3
    )
    assert [item["job_id"] for item in including_closed["items"]] == [2, 4]


def test_category_counts_are_zero_for_empty_results(settings: Settings) -> None:
    seed_resume(settings, queued=False)
    data = TestClient(app).get("/resumes/resume/match-results?category=good").json()
    assert data["good_total"] == data["other_total"] == data["total"] == 0
    assert data["items"] == [] and data["has_more"] is False
