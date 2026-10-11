import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_tailor import CV, complete_plan

from backend.config import Settings
from backend.db import SCHEMA_VERSION, connect_app
from backend.main import app
from backend.matching.jev import JobScore
from backend.resumes import (
    artifact_queue,
    artifact_store,
    artifacts,
    design,
    pipeline,
    queue,
    render,
    tailor,
)
from backend.resumes.storage import job_key


def seed(settings: Settings, *, pdf: bytes | None = None) -> list[str]:
    conn = connect_app(settings.app_db)
    conn.execute(
        "INSERT INTO resumes (id, created_at, pdf_sha256, status, rendercv_yaml, upload_pdf) VALUES ('resume', 'today', 'pdfhash', 'done', ?, ?)",
        (CV, pdf),
    )
    keys = []
    for index in [1, 2]:
        job = {
            "id": index,
            "provider": "test",
            "board": "board",
            "source_id": str(index),
            "content_hash": f"hash{index}",
            "title": f"Thermal Engineer {index}",
            "company": "Employer",
            "description_text": "Mechanical thermal modeling and CAD",
            "locations": ["Canada"],
        }
        value = JobScore(
            index, job["content_hash"], 0.9, 3.8, 0.9, 0.9, 0.9, "matches", 100
        )
        pipeline.save_score(conn, "resume", 0, 0, job, value)
        keys.append(job_key(job))
    conn.commit()
    conn.close()
    return keys


def run_lane(settings: Settings, kind: str) -> None:
    assert kind in {"design", "tailor"}
    claim = artifact_queue.claim_artifact(
        settings, "design" if kind == "design" else "tailor"
    )
    assert claim is not None
    asyncio.run(artifact_queue.run_artifact_claim(settings, claim))


@pytest.fixture
def mocks(monkeypatch, make_pdf):
    calls = {"design": 0, "tailor": 0, "render": 0}

    async def reconstruct(pdf, yaml, settings):
        calls["design"] += 1
        return SimpleNamespace(
            design=design.default_design(),
            score=0.93,
            iterations=2,
            notes="reviewed",
            cost_usd=0.01,
        )

    async def choose(yaml, job, settings):
        calls["tailor"] += 1
        plan = complete_plan(yaml)
        return tailor.TailoringResult(
            plan=plan, yaml=tailor.apply_plan(yaml, plan), cost_usd=0.002
        )

    async def pdf(yaml, selected_design, **kwargs):
        calls["render"] += 1
        return render.RenderedResume(pdf=make_pdf(), pages=(), page_count=1)

    monkeypatch.setattr(design, "reconstruct_design", reconstruct)
    monkeypatch.setattr(artifacts, "tailor_resume", choose)
    monkeypatch.setattr(render, "render_resume", pdf)
    return calls


def test_design_tailoring_and_matching_have_independent_lanes_and_durable_cache(
    settings: Settings, mocks, make_pdf
) -> None:
    keys = seed(settings, pdf=make_pdf())
    client = TestClient(app)
    created = client.post("/resumes/resume/tailor", json={"job_key": keys[0]})
    assert created.status_code == 202
    artifact = created.json()["id"]
    assert (
        client.post("/resumes/resume/tailor", json={"job_key": keys[0]}).json()["id"]
        == artifact
    )
    conn = connect_app(settings.app_db)
    queue.enqueue(conn, "resume")
    conn.commit()
    matching_claim = queue.claim_next(settings)
    assert matching_claim is not None
    # Tailoring waits without occupying matching or design lanes.
    run_lane(settings, "tailor")
    assert (
        client.get(f"/resumes/resume/tailored/{artifact}").json()["status"]
        == "waiting_for_design"
    )
    assert mocks == {"design": 0, "tailor": 0, "render": 0}
    run_lane(settings, "design")
    run_lane(settings, "tailor")
    ready = client.get(f"/resumes/resume/tailored/{artifact}").json()
    assert ready["status"] == "ready" and ready["design_source"] == "reconstructed"
    download = client.get(ready["download_url"])
    assert download.content.startswith(b"%PDF-")
    assert download.headers["cache-control"] == "private, no-store"
    assert download.headers["x-content-type-options"] == "nosniff"
    assert "filename*=UTF-8" in download.headers["content-disposition"]
    assert client.get("/resumes/resume/design").json()["quality_score"] == 0.93
    queue.fence(
        conn, matching_claim
    )  # artifact processing left matching lease untouched
    # A new client/restart reuses saved PDF, without any model or renderer request.
    cached = (
        TestClient(app).post("/resumes/resume/tailor", json={"job_key": keys[0]}).json()
    )
    assert cached["id"] == artifact and cached["status"] == "ready"
    assert mocks == {"design": 1, "tailor": 1, "render": 1}
    another = client.post("/resumes/resume/tailor", json={"job_key": keys[1]}).json()
    run_lane(settings, "tailor")
    assert another["id"] != artifact and mocks["design"] == 1 and mocks["tailor"] == 2
    conn.close()


def test_legacy_cv_uses_explicit_default_design_without_reconstruction(
    settings: Settings, mocks
) -> None:
    key = seed(settings)[0]
    client = TestClient(app)
    artifact = client.post("/resumes/resume/tailor", json={"job_key": key}).json()["id"]
    run_lane(settings, "design")
    run_lane(settings, "tailor")
    result = client.get(f"/resumes/resume/tailored/{artifact}").json()
    assert result["status"] == "ready" and result["design_source"] == "default"
    assert mocks["design"] == 0


def test_new_tailoring_version_reuses_design_without_hiding_old_pdf_or_double_counting(
    settings: Settings, mocks, make_pdf, monkeypatch
) -> None:
    key = seed(settings, pdf=make_pdf())[0]
    client = TestClient(app)
    first = client.post("/resumes/resume/tailor", json={"job_key": key}).json()["id"]
    run_lane(settings, "design")
    run_lane(settings, "tailor")
    first_url = f"/resumes/resume/tailored/{first}/cv.pdf"
    old_pdf = client.get(first_url)
    assert old_pdf.status_code == 200 and old_pdf.content.startswith(b"%PDF-")
    savings = client.get("/resumes/resume/time-savings").json()
    assert savings["cvs_tailored"] == 1
    conn = connect_app(settings.app_db)
    selected_design = tuple(
        conn.execute("SELECT id, design_json FROM resume_designs").fetchone()
    )

    # A deployed tailoring revision changes the cache identity, not the design.
    updated_version = f"{tailor.TAILOR_VERSION}-next"
    for module in (tailor, artifact_store, artifacts):
        monkeypatch.setattr(module, "TAILOR_VERSION", updated_version)
    created = client.post("/resumes/resume/tailor", json={"job_key": key})
    second = created.json()["id"]
    assert created.status_code == 202 and second != first
    assert (
        client.post("/resumes/resume/tailor", json={"job_key": key}).json()["id"]
        == second
    )
    assert client.get(first_url).content == old_pdf.content
    assert client.get("/resumes/resume/time-savings").json() == savings
    run_lane(settings, "tailor")
    assert client.get(f"/resumes/resume/tailored/{second}").json()["status"] == "ready"
    assert client.get(f"/resumes/resume/tailored/{second}/cv.pdf").status_code == 200
    assert client.get(first_url).content == old_pdf.content
    assert client.get("/resumes/resume/time-savings").json() == savings
    assert conn.execute("SELECT COUNT(*) FROM resume_designs").fetchone()[0] == 1
    assert (
        tuple(conn.execute("SELECT id, design_json FROM resume_designs").fetchone())
        == selected_design
    )
    assert conn.execute("SELECT COUNT(*) FROM tailored_cvs").fetchone()[0] == 2
    assert (
        conn.execute(
            "SELECT version FROM tailored_cvs WHERE id = ?", (second,)
        ).fetchone()[0]
        == updated_version
    )
    assert (
        client.post("/resumes/resume/tailor", json={"job_key": key}).json()["id"]
        == second
    )
    assert mocks == {"design": 1, "tailor": 2, "render": 2}
    conn.close()


def test_render_failure_retries_from_factual_yaml_checkpoint(
    settings: Settings, mocks, monkeypatch
) -> None:
    key = seed(settings)[0]
    client = TestClient(app)
    artifact = client.post("/resumes/resume/tailor", json={"job_key": key}).json()["id"]
    run_lane(settings, "design")
    real_mock = render.render_resume

    async def fail(*args, **kwargs):
        raise RuntimeError("renderer temporarily unavailable")

    monkeypatch.setattr(render, "render_resume", fail)
    run_lane(settings, "tailor")
    assert (
        client.get(f"/resumes/resume/tailored/{artifact}").json()["status"] == "failed"
    )
    assert mocks["tailor"] == 1
    conn = connect_app(settings.app_db)
    saved = conn.execute("SELECT tailored_yaml, plan_json FROM tailored_cvs").fetchone()
    assert saved["tailored_yaml"] and saved["plan_json"]
    conn.close()
    monkeypatch.setattr(render, "render_resume", real_mock)
    assert (
        client.post("/resumes/resume/tailor", json={"job_key": key}).json()["id"]
        == artifact
    )
    run_lane(settings, "tailor")
    assert mocks["tailor"] == 1 and mocks["render"] == 1
    assert (
        client.get(f"/resumes/resume/tailored/{artifact}").json()["status"] == "ready"
    )


def test_duplicate_concurrent_requests_are_atomic_and_queue_is_bounded(
    settings: Settings, mocks, monkeypatch
) -> None:
    keys = seed(settings)
    monkeypatch.setattr(settings, "artifact_queue_max_pending", 1)

    def create():
        response = TestClient(app).post(
            "/resumes/resume/tailor", json={"job_key": keys[0]}
        )
        assert response.status_code == 202
        return response.json()["id"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(lambda _: create(), range(2)))
    assert ids[0] == ids[1]
    assert (
        TestClient(app)
        .post("/resumes/resume/tailor", json={"job_key": keys[1]})
        .status_code
        == 429
    )
    conn = connect_app(settings.app_db)
    assert conn.execute("SELECT COUNT(*) FROM tailored_cvs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM resume_designs").fetchone()[0] == 1
    conn.close()


def test_pdf_access_requires_matching_resume_and_delete_cascades(
    settings: Settings, mocks
) -> None:
    key = seed(settings)[0]
    client = TestClient(app)
    artifact = client.post("/resumes/resume/tailor", json={"job_key": key}).json()["id"]
    assert client.get(f"/resumes/resume/tailored/{artifact}/cv.pdf").status_code == 409
    assert client.get(f"/resumes/another/tailored/{artifact}").status_code == 404
    assert client.get(f"/resumes/another/tailored/{artifact}/cv.pdf").status_code == 404
    assert client.delete("/resumes/resume").status_code == 204
    conn = connect_app(settings.app_db)
    for table in ["resume_designs", "tailored_cvs", "artifact_queue"]:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    conn.close()


def test_artifact_lease_recovery_fences_previous_worker(
    settings: Settings, mocks
) -> None:
    key = seed(settings)[0]
    TestClient(app).post("/resumes/resume/tailor", json={"job_key": key})
    old = artifact_queue.claim_artifact(settings, "design")
    assert old is not None
    assert artifact_queue.claim_artifact(settings, "design") is None
    # The other lane can be claimed concurrently.
    assert artifact_queue.claim_artifact(settings, "tailor") is not None
    conn = connect_app(settings.app_db)
    conn.execute(
        "UPDATE artifact_queue SET lease_until = ? WHERE kind = 'design'",
        (time.time() - 1,),
    )
    conn.commit()
    current = artifact_queue.claim_artifact(settings, "design")
    assert current is not None and current.owner != old.owner
    with pytest.raises(artifact_queue.ArtifactLeaseLost):
        artifact_queue.artifact_fence(conn, old)
    artifact_queue.artifact_fence(conn, current)
    conn.close()


def test_rematch_location_uses_same_design_and_new_candidate_fingerprint(
    settings: Settings, mocks
) -> None:
    key = seed(settings)[0]
    client = TestClient(app)
    first = client.post("/resumes/resume/tailor", json={"job_key": key}).json()["id"]
    run_lane(settings, "design")
    run_lane(settings, "tailor")
    conn = connect_app(settings.app_db)
    before = conn.execute("SELECT id FROM resume_designs").fetchone()[0]
    assert (
        client.post(
            "/resumes/resume/rematch", json={"location": "Calgary, Canada"}
        ).status_code
        == 202
    )
    second = client.post("/resumes/resume/tailor", json={"job_key": key}).json()["id"]
    assert second != first
    assert conn.execute("SELECT id FROM resume_designs").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM resume_designs").fetchone()[0] == 1
    assert mocks["design"] == 0
    conn.close()


def test_bad_job_reference_never_queues_work(settings: Settings) -> None:
    seed(settings)
    client = TestClient(app)
    assert (
        client.post(
            "/resumes/resume/tailor", json={"job_key": "not-a-saved-match"}
        ).status_code
        == 404
    )
    assert (
        client.post("/resumes/resume/tailor", json={"job_key": "x" * 257}).status_code
        == 422
    )
    conn = connect_app(settings.app_db)
    assert conn.execute("SELECT COUNT(*) FROM artifact_queue").fetchone()[0] == 0
    conn.execute("UPDATE matches SET job_snapshot = NULL")
    conn.commit()
    key = conn.execute("SELECT job_key FROM matches LIMIT 1").fetchone()[0]
    invalid = client.post("/resumes/resume/tailor", json={"job_key": key})
    assert invalid.status_code == 422 and "Refresh matching" in invalid.json()["detail"]
    conn.close()


def test_v2_migration_is_additive_and_retains_saved_results(settings: Settings) -> None:
    seed(settings)
    conn = connect_app(settings.app_db)
    for table in ["artifact_queue", "tailored_cvs", "resume_designs"]:
        conn.execute(f"DROP TABLE {table}")
    conn.execute("PRAGMA user_version = 2")
    before = [
        tuple(row) for row in conn.execute("SELECT * FROM matches ORDER BY job_key")
    ]
    conn.commit()
    conn.close()
    upgraded = connect_app(settings.app_db)
    assert upgraded.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert [
        tuple(row) for row in upgraded.execute("SELECT * FROM matches ORDER BY job_key")
    ] == before
    assert upgraded.execute("SELECT COUNT(*) FROM tailored_cvs").fetchone()[0] == 0
    upgraded.close()


def test_shutdown_recovers_render_checkpoint_without_repeating_tailoring(
    settings: Settings, mocks, monkeypatch
) -> None:
    key = seed(settings)[0]
    client = TestClient(app)
    artifact = client.post("/resumes/resume/tailor", json={"job_key": key}).json()["id"]
    run_lane(settings, "design")
    normal_render = render.render_resume

    async def scenario():
        entered_render = asyncio.Event()
        never = asyncio.Event()
        cancelled = asyncio.Event()

        async def interrupted(*args, **kwargs):
            entered_render.set()
            try:
                await never.wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(render, "render_resume", interrupted)
        claim = artifact_queue.claim_artifact(settings, "tailor")
        assert claim is not None
        running = asyncio.create_task(
            artifact_queue.run_artifact_claim(settings, claim)
        )
        await entered_render.wait()
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        assert cancelled.is_set()
        conn = connect_app(settings.app_db)
        assert (
            conn.execute(
                "SELECT state FROM artifact_queue WHERE kind = 'tailor'"
            ).fetchone()[0]
            == "pending"
        )
        assert conn.execute("SELECT tailored_yaml FROM tailored_cvs").fetchone()[0]
        conn.close()
        monkeypatch.setattr(render, "render_resume", normal_render)
        reclaimed = artifact_queue.claim_artifact(settings, "tailor")
        assert reclaimed is not None
        await artifact_queue.run_artifact_claim(settings, reclaimed)

    asyncio.run(scenario())
    assert mocks["tailor"] == 1 and mocks["render"] == 1
    assert (
        client.get(f"/resumes/resume/tailored/{artifact}").json()["status"] == "ready"
    )


def test_failed_design_and_dependent_tailoring_retry_together(
    settings: Settings, mocks, make_pdf, monkeypatch
) -> None:
    key = seed(settings, pdf=make_pdf())[0]
    client = TestClient(app)
    artifact = client.post("/resumes/resume/tailor", json={"job_key": key}).json()["id"]
    normal_design = design.reconstruct_design

    async def failed(*args, **kwargs):
        raise RuntimeError("temporary design provider outage")

    monkeypatch.setattr(design, "reconstruct_design", failed)
    run_lane(settings, "design")
    run_lane(settings, "tailor")
    assert (
        client.get(f"/resumes/resume/tailored/{artifact}").json()["status"] == "failed"
    )
    assert client.get("/resumes/resume/design").json()["status"] == "failed"
    assert mocks["tailor"] == 0
    monkeypatch.setattr(design, "reconstruct_design", normal_design)
    retry = client.post("/resumes/resume/tailor", json={"job_key": key})
    assert retry.status_code == 202 and retry.json()["id"] == artifact
    run_lane(settings, "design")
    run_lane(settings, "tailor")
    assert (
        client.get(f"/resumes/resume/tailored/{artifact}").json()["status"] == "ready"
    )
    assert mocks["design"] == mocks["tailor"] == 1


def test_queued_requests_use_frozen_models_and_new_model_gets_new_cache_entry(
    settings: Settings, mocks, make_pdf, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "design_model", "saved-design-model")
    monkeypatch.setattr(settings, "tailor_model", "saved-tailor-model")
    key = seed(settings, pdf=make_pdf())[0]
    client = TestClient(app)
    artifact = client.post("/resumes/resume/tailor", json={"job_key": key}).json()["id"]
    used = []
    normal_design, normal_tailor = design.reconstruct_design, artifacts.tailor_resume

    async def record_design(pdf, yaml, selected_settings):
        used.append(selected_settings.design_model)
        return await normal_design(pdf, yaml, selected_settings)

    async def record_tailor(yaml, job, selected_settings):
        used.append(selected_settings.tailor_model)
        return await normal_tailor(yaml, job, selected_settings)

    monkeypatch.setattr(design, "reconstruct_design", record_design)
    monkeypatch.setattr(artifacts, "tailor_resume", record_tailor)
    monkeypatch.setattr(settings, "design_model", "new-design-model")
    monkeypatch.setattr(settings, "tailor_model", "new-tailor-model")
    run_lane(settings, "design")
    run_lane(settings, "tailor")
    assert used == ["saved-design-model", "saved-tailor-model"]
    new = client.post("/resumes/resume/tailor", json={"job_key": key}).json()["id"]
    assert new != artifact
    conn = connect_app(settings.app_db)
    assert conn.execute("SELECT COUNT(*) FROM resume_designs").fetchone()[0] == 1
    conn.close()


def test_download_filename_is_bounded_and_cannot_inject_paths_or_headers() -> None:
    import re

    filename = artifacts.pdf_filename(
        CV, {"company": '../Company/";\r\nInjected: x', "title": "Role" * 100}
    )
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,150}\.pdf", filename)
