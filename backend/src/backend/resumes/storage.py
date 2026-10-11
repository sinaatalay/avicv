"""Immutable job identities, snapshots, and compatibility for historical matches."""

import hashlib
import json
import sqlite3
from typing import Any

from ..config import Settings
from ..db import connect, connect_app
from ..matching.jev import job_state


def job_key(job: dict[str, Any]) -> str:
    identity = {
        "source": [
            job["provider"],
            job["board"],
            job["source_id"],
            job["content_hash"],
        ],
        "model": job.get("scoring_model"),
        "questions": job.get("questions_version"),
        "input": job_state(job),
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def snapshot(job: dict[str, Any]) -> str:
    """Persist the exact scoring input plus original display fields and source identity."""
    return json.dumps(
        {key: value for key, value in job.items() if key != "raw_zstd"},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def backfill_snapshots(settings: Settings) -> None:
    """Recover legacy metadata only when numeric ID AND the scored hash still match.

    Unverifiable rows remain intact and readable with unavailable original metadata.
    This operation never guesses identity from a reused numeric ID.
    """
    if not settings.jobs_db.exists():
        return
    conn = connect_app(settings.app_db)
    jobs = connect(settings.jobs_db, readonly=True)
    try:
        for saved in conn.execute(
            "SELECT rowid, * FROM matches WHERE job_snapshot IS NULL"
        ).fetchall():
            row = jobs.execute(
                "SELECT * FROM jobs WHERE id = ? AND content_hash = ?",
                (saved["job_id"], saved["job_hash"]),
            ).fetchone()
            if row is None:
                continue
            job = dict(row) | {"locations": json.loads(saved["locations"])}
            conn.execute(
                "UPDATE matches SET job_snapshot = ?, provider = ?, board = ?, source_id = ?, job_key = ? WHERE rowid = ?",
                (
                    snapshot(job),
                    job["provider"],
                    job["board"],
                    job["source_id"],
                    job_key(job),
                    saved["rowid"],
                ),
            )
        conn.commit()
    except sqlite3.OperationalError:
        # The first catalog synchronization may still be creating its schema.
        conn.rollback()
    finally:
        jobs.close()
        conn.close()


def refresh_progress(conn: sqlite3.Connection, resume_id: str) -> dict[str, int]:
    counts = conn.execute(
        """
        SELECT COUNT(*) AS total,
            COALESCE(SUM(w.state = 'succeeded'), 0) AS succeeded,
            COALESCE(SUM(w.state = 'failed'), 0) AS failed,
            COALESCE(SUM(w.attempts > 0), 0) AS attempted
        FROM resume_work w JOIN resumes r ON r.id = w.resume_id
        WHERE w.resume_id = ? AND w.generation = r.work_generation
            AND w.candidate_revision = r.candidate_revision
    """,
        (resume_id,),
    ).fetchone()
    values = dict(counts)
    conn.execute(
        "UPDATE resumes SET match_total = ?, match_done = ?, match_failed = ?, match_attempted = ? WHERE id = ?",
        (
            values["total"],
            values["succeeded"],
            values["failed"],
            values["attempted"],
            resume_id,
        ),
    )
    return values
