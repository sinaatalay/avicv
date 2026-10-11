import json
import sqlite3
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from test_resume_reliability import role, score, seed_resume

from backend.config import Settings
from backend.db import connect_app
from backend.main import app
from backend.resumes import pipeline
from backend.resumes.storage import job_key


def save_artifact(
    conn: sqlite3.Connection,
    job: dict[str, Any],
    *,
    resume_id: str = "resume",
    status: str = "ready",
    pdf: bytes | None = b"%PDF-1.7\ntest",
) -> str:
    conn.execute(
        """
        INSERT OR IGNORE INTO resume_designs
            (id, resume_id, created_at, updated_at, version, model, source_yaml, source_pdf_hash, status)
        VALUES (?, ?, 'today', 'today', '1', 'test-model', 'source', 'pdfhash', 'ready')
    """,
        (f"design-{resume_id}", resume_id),
    )
    artifact_id = uuid4().hex
    conn.execute(
        """
        INSERT INTO tailored_cvs
            (id, resume_id, design_id, fingerprint, job_key, source_yaml, job_snapshot,
             model, version, render_version, created_at, updated_at, status, pdf_bytes)
        VALUES (?, ?, ?, ?, ?, 'source', ?, 'test-model', '1', '1', 'today', 'today', ?, ?)
    """,
        (
            artifact_id,
            resume_id,
            f"design-{resume_id}",
            artifact_id,
            job_key(job) if job.get("provider") else "unknown",
            json.dumps(job),
            status,
            pdf,
        ),
    )
    return artifact_id


def test_time_savings_counts_unique_successes_across_all_saved_versions(
    settings: Settings,
) -> None:
    seed_resume(settings, queued=False)
    conn = connect_app(settings.app_db)
    for revision, generation, model, content_hash in [
        (0, 0, "model-one", "old-content"),
        (0, 1, "model-two", "old-content"),
        (1, 2, "model-two", "new-content"),
    ]:
        job = role(1) | {"scoring_model": model, "content_hash": content_hash}
        pipeline.save_score(conn, "resume", revision, generation, job, score(job))
    other_job = role(2)
    pipeline.save_score(conn, "resume", 0, 2, other_job, score(other_job))
    # Failed and pending scoring work are not completed reviews.
    for state, index in [("failed", 3), ("pending", 4)]:
        job = role(index)
        conn.execute(
            """
            INSERT INTO resume_work (resume_id, job_key, candidate_revision, questions,
                generation, job_snapshot, state, attempts)
            VALUES ('resume', ?, 0, 'test', 2, ?, ?, 3)
        """,
            (job_key(job), json.dumps(job), state),
        )
    conn.execute("UPDATE resumes SET current_generation = 2 WHERE id = 'resume'")
    conn.commit()
    conn.close()
    client = TestClient(app)
    response = client.get("/resumes/resume/time-savings")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == {
        "jobs_reviewed": 2,
        "cvs_tailored": 0,
        "job_review_seconds": 30,
        "cv_tailoring_seconds": 600,
        "estimated_seconds": 60,
    }
    # Polling and rebuilding the client change no authoritative counts.
    assert TestClient(app).get("/resumes/resume/time-savings").json() == response.json()


def test_time_savings_counts_only_distinct_completed_pdfs_for_this_resume(
    settings: Settings,
) -> None:
    seed_resume(settings, queued=False)
    seed_resume(settings, "other", queued=False)
    conn = connect_app(settings.app_db)
    first = role(1)
    second = role(2)
    for job in [first, second]:
        pipeline.save_score(conn, "resume", 0, 0, job, score(job))
    first_id = save_artifact(conn, first)
    duplicate = save_artifact(conn, first | {"content_hash": "changed-content"})
    conn.execute(
        "UPDATE tailored_cvs SET model = 'new-model', version = '2', render_version = '2' WHERE id = ?",
        (duplicate,),
    )
    save_artifact(conn, second)
    for status in ["queued", "waiting_for_design", "tailoring", "rendering", "failed"]:
        save_artifact(conn, role(3), status=status)
    save_artifact(conn, role(4), pdf=None)
    save_artifact(conn, role(5), pdf=b"")
    save_artifact(conn, role(6), pdf=b"invalid")
    foreign = role(99)
    pipeline.save_score(conn, "other", 0, 0, foreign, score(foreign))
    save_artifact(conn, foreign, resume_id="other")
    conn.commit()
    conn.close()
    client = TestClient(app)
    result = client.get("/resumes/resume/time-savings").json()
    assert result["jobs_reviewed"] == result["cvs_tailored"] == 2
    assert result["estimated_seconds"] == 2 * 30 + 2 * 600
    # Downloads are irrelevant to the estimate.
    for _ in range(2):
        assert (
            client.get(f"/resumes/resume/tailored/{first_id}/cv.pdf").status_code == 200
        )
    assert client.get("/resumes/resume/time-savings").json() == result
    other = client.get("/resumes/other/time-savings").json()
    assert other["jobs_reviewed"] == other["cvs_tailored"] == 1
    assert other["estimated_seconds"] == 630


def test_time_savings_groups_explicit_members_when_representative_changes(
    settings: Settings,
) -> None:
    seed_resume(settings, queued=False)
    conn = connect_app(settings.app_db)
    first = role(1) | {"postings": [role(1), role(2)]}
    second = role(2) | {"postings": [role(2), role(3)]}
    for generation, job in enumerate([first, second, role(3)]):
        pipeline.save_score(conn, "resume", generation, generation, job, score(job))
        save_artifact(conn, job)
    unrelated = role(4)
    pipeline.save_score(conn, "resume", 0, 0, unrelated, score(unrelated))
    conn.commit()
    conn.close()
    result = TestClient(app).get("/resumes/resume/time-savings").json()
    assert result["jobs_reviewed"] == 2
    assert result["cvs_tailored"] == 1
    assert result["estimated_seconds"] == 660


def test_time_savings_legacy_fallback_never_guesses_from_reused_numeric_ids(
    settings: Settings,
) -> None:
    seed_resume(settings, queued=False)
    conn = connect_app(settings.app_db)
    snapshots = [
        None,
        {"id": 1, "content_hash": "new-hash"},
        {"id": 1, "source_url": "https://example.com/legacy"},
        {"id": 99, "source_url": "https://example.com/legacy"},
        {"id": 99, "source_url": "javascript:unsafe"},
        {"id": 1, "source_url": "https://example.com/known"},
    ]
    for index, snapshot in enumerate(snapshots):
        job = role(1) | {"content_hash": f"legacy-{index}"}
        pipeline.save_score(conn, "resume", index, index, job, score(job))
        conn.execute(
            """
            UPDATE matches SET provider = NULL, board = NULL, source_id = NULL, job_snapshot = ?
            WHERE candidate_revision = ?
        """,
            (json.dumps(snapshot) if snapshot is not None else None, index),
        )
    known = role(2) | {"source_url": "https://example.com/known"}
    pipeline.save_score(conn, "resume", 0, 0, known, score(known))
    # URL-only duplicate artifacts likewise count once; no identity/URL counts zero.
    save_artifact(conn, {"id": 1, "source_url": "https://example.com/legacy"})
    save_artifact(conn, {"id": 99, "source_url": "https://example.com/legacy"})
    save_artifact(conn, {"id": 1})
    conn.commit()
    conn.close()
    result = TestClient(app).get("/resumes/resume/time-savings").json()
    assert result["jobs_reviewed"] == 2  # one known job, one URL-only legacy job
    assert result["cvs_tailored"] == 1


def test_time_savings_returns_zero_for_empty_resume_and_404_for_unknown(
    settings: Settings,
) -> None:
    seed_resume(settings, queued=False)
    client = TestClient(app)
    result = client.get("/resumes/resume/time-savings").json()
    assert (
        result["jobs_reviewed"]
        == result["cvs_tailored"]
        == result["estimated_seconds"]
        == 0
    )
    assert client.get("/resumes/unknown/time-savings").status_code == 404
