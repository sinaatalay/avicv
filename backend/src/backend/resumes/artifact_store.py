"""Durable, idempotent design and tailored-PDF requests."""

import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from ..config import Settings
from .tailor import TAILOR_VERSION

ArtifactKind = Literal["design", "tailor"]


class ArtifactQueueFull(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def enqueue_artifact(
    conn: sqlite3.Connection, kind: ArtifactKind, target_id: str, resume_id: str
) -> None:
    conn.execute(
        """
        INSERT INTO artifact_queue (kind, target_id, resume_id, state, available_at)
        VALUES (?, ?, ?, 'pending', ?)
        ON CONFLICT (kind, target_id) DO UPDATE SET state = 'pending', available_at = excluded.available_at,
            owner = NULL, lease_until = NULL
    """,
        (kind, target_id, resume_id, time.time()),
    )


def check_artifact_capacity(
    conn: sqlite3.Connection,
    settings: Settings,
    kind: ArtifactKind,
    resume_id: str = "",
) -> None:
    if kind == "design":
        # Accepted matching work reserves future design capacity, so parsing can
        # enqueue its design without blocking or dropping the accepted work.
        count = conn.execute(
            """
            SELECT COUNT(DISTINCT resume_id) FROM (
                SELECT resume_id FROM artifact_queue WHERE kind = 'design' AND state != 'done'
                UNION SELECT resume_id FROM resume_queue WHERE state != 'done'
            ) WHERE resume_id != ?
        """,
            (resume_id,),
        ).fetchone()[0]
    else:
        count = conn.execute(
            "SELECT COUNT(*) FROM artifact_queue WHERE kind = 'tailor' AND state != 'done'"
        ).fetchone()[0]
    if count >= settings.artifact_queue_max_pending:
        raise ArtifactQueueFull("The CV preparation queue is full. Try again shortly.")


def ensure_design(
    conn: sqlite3.Connection,
    resume_id: str,
    settings: Settings,
    *,
    retry_failed: bool = False,
    accepted_upload: bool = False,
) -> sqlite3.Row:
    """One selected design per résumé, independent of later matching/location changes."""
    existing = conn.execute(
        "SELECT * FROM resume_designs WHERE resume_id = ?", (resume_id,)
    ).fetchone()
    if existing:
        if existing["status"] == "failed" and retry_failed:
            check_artifact_capacity(conn, settings, "design", resume_id)
            conn.execute(
                "UPDATE resume_designs SET status = 'queued', error = NULL, updated_at = ? WHERE id = ?",
                (now_iso(), existing["id"]),
            )
            enqueue_artifact(conn, "design", existing["id"], resume_id)
            return conn.execute(
                "SELECT * FROM resume_designs WHERE id = ?", (existing["id"],)
            ).fetchone()
        return existing
    if not accepted_upload:
        check_artifact_capacity(conn, settings, "design", resume_id)
    from .design import DESIGN_VERSION

    source = conn.execute(
        "SELECT rendercv_yaml, pdf_sha256 FROM resumes WHERE id = ?", (resume_id,)
    ).fetchone()
    if source is None or not source["rendercv_yaml"]:
        raise ValueError("Wait for résumé parsing to finish before tailoring a CV.")
    design_id = uuid4().hex
    now = now_iso()
    conn.execute(
        """
        INSERT INTO resume_designs (id, resume_id, created_at, updated_at, version, model,
            source_yaml, source_pdf_hash, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued')
    """,
        (
            design_id,
            resume_id,
            now,
            now,
            DESIGN_VERSION,
            settings.design_model,
            source["rendercv_yaml"],
            source["pdf_sha256"],
        ),
    )
    enqueue_artifact(conn, "design", design_id, resume_id)
    return conn.execute(
        "SELECT * FROM resume_designs WHERE id = ?", (design_id,)
    ).fetchone()


def request_tailoring(
    conn: sqlite3.Connection, resume_id: str, job_key: str, settings: Settings
) -> sqlite3.Row:
    """Caller holds BEGIN IMMEDIATE: concurrent duplicate requests share one artifact."""
    from .render import RENDER_VERSION

    source = conn.execute(
        "SELECT rendercv_yaml FROM resumes WHERE id = ?", (resume_id,)
    ).fetchone()
    if source is None:
        raise LookupError("Résumé not found.")
    if not source["rendercv_yaml"]:
        raise ValueError("Wait for résumé parsing to finish before tailoring a CV.")
    match = conn.execute(
        """
        SELECT job_snapshot FROM matches WHERE resume_id = ? AND job_key = ?
        ORDER BY generation DESC, candidate_revision DESC LIMIT 1
    """,
        (resume_id, job_key),
    ).fetchone()
    if match is None:
        raise LookupError("This job isn't a saved match for the résumé.")
    if not match["job_snapshot"]:
        raise ValueError(
            "Job details unavailable. Refresh matching before tailoring this CV."
        )
    job = json.loads(match["job_snapshot"])
    if not job.get("description_text"):
        raise ValueError("This job has no saved description to tailor against.")
    design = ensure_design(conn, resume_id, settings, retry_failed=True)
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "source_yaml": source["rendercv_yaml"],
                "job_snapshot": job,
                "design_id": design["id"],
                "design_version": design["version"],
                "tailor_version": TAILOR_VERSION,
                "model": settings.tailor_model,
                "render_version": RENDER_VERSION,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    existing = conn.execute(
        "SELECT * FROM tailored_cvs WHERE resume_id = ? AND fingerprint = ?",
        (resume_id, fingerprint),
    ).fetchone()
    if existing:
        if existing["status"] == "failed":
            check_artifact_capacity(conn, settings, "tailor")
            conn.execute(
                "UPDATE tailored_cvs SET status = 'queued', error = NULL, updated_at = ? WHERE id = ?",
                (now_iso(), existing["id"]),
            )
            enqueue_artifact(conn, "tailor", existing["id"], resume_id)
        return fetch_tailored(conn, resume_id, existing["id"])
    check_artifact_capacity(conn, settings, "tailor")
    artifact_id = uuid4().hex
    now = now_iso()
    conn.execute(
        """
        INSERT INTO tailored_cvs (id, resume_id, design_id, fingerprint, job_key, source_yaml,
            job_snapshot, model, version, render_version, created_at, updated_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued')
    """,
        (
            artifact_id,
            resume_id,
            design["id"],
            fingerprint,
            job_key,
            source["rendercv_yaml"],
            match["job_snapshot"],
            settings.tailor_model,
            TAILOR_VERSION,
            RENDER_VERSION,
            now,
            now,
        ),
    )
    enqueue_artifact(conn, "tailor", artifact_id, resume_id)
    return fetch_tailored(conn, resume_id, artifact_id)


def fetch_tailored(
    conn: sqlite3.Connection, resume_id: str, artifact_id: str
) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT t.id, t.resume_id, t.status, t.error, t.created_at, t.updated_at, t.job_key,
            t.filename, d.source AS design_source FROM tailored_cvs t
        JOIN resume_designs d ON d.id = t.design_id WHERE t.resume_id = ? AND t.id = ?
    """,
        (resume_id, artifact_id),
    ).fetchone()
    if row is None:
        raise LookupError("Tailored CV not found.")
    return row


def design_status(conn: sqlite3.Connection, resume_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, status, source, error, quality_score, iterations FROM resume_designs WHERE resume_id = ?",
        (resume_id,),
    ).fetchone()
    return dict(row) if row else None
