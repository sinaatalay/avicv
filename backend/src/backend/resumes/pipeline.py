"""Durable parse/retrieve/score processing, resumed from committed checkpoints."""

import asyncio
import json
import logging
import sqlite3
import time
from datetime import UTC, datetime
from typing import Any

from ..config import Settings
from ..db import connect_app
from ..matching import jev
from ..matching.prefilter import shortlist_with_diagnostics
from .artifact_store import ensure_design
from .parse import load_yaml, parse_resume, preview
from .queue import Claim, LeaseLost, fence
from .storage import job_key, refresh_progress, snapshot

log = logging.getLogger(__name__)
PROGRESS_INTERVAL = 0.5


def begin(conn: sqlite3.Connection, claim: Claim) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        fence(conn, claim)
    except BaseException:
        conn.rollback()
        raise


async def process_resume(claim: Claim, settings: Settings) -> float | None:
    """Return a delay when waiting for the catalog; otherwise this queue item is done."""
    conn = connect_app(settings.app_db)
    resume_id = claim.resume_id
    preview_at, preview_json = 0.0, ""

    def show_progress(partial_yaml: str) -> None:
        nonlocal preview_at, preview_json
        if time.monotonic() - preview_at < PROGRESS_INTERVAL:
            return
        preview_at = time.monotonic()
        value = json.dumps(preview(partial_yaml))
        if value != preview_json:
            begin(conn, claim)
            conn.execute(
                "UPDATE resumes SET parse_preview = ? WHERE id = ?", (value, resume_id)
            )
            conn.commit()
            preview_json = value

    try:
        row = conn.execute(
            "SELECT * FROM resumes WHERE id = ?", (resume_id,)
        ).fetchone()
        if row is None:
            raise LeaseLost("Résumé was deleted")
        if not row["rendercv_yaml"]:
            if not row["upload_pdf"]:
                fail(
                    conn,
                    claim,
                    "The original PDF is unavailable. Please upload it again.",
                )
                return None
            begin(conn, claim)
            conn.execute(
                "UPDATE resumes SET status = 'parsing', error = NULL WHERE id = ?",
                (resume_id,),
            )
            conn.commit()
            try:
                parsed = await parse_resume(
                    row["upload_pdf"], settings, on_text=show_progress
                )
            except LeaseLost:
                raise
            except Exception:
                log.exception("Parsing résumé %s failed", resume_id)
                fail(conn, claim, "We couldn't read a résumé from this PDF.")
                return None
            begin(conn, claim)
            conn.execute(
                "UPDATE resumes SET rendercv_yaml = ?, parse_seconds = ?, parse_cost_usd = ? WHERE id = ?",
                (parsed.yaml, parsed.seconds, parsed.cost_usd, resume_id),
            )
            # This durable design request is committed with parsing. Its separate
            # worker lane cannot hold up shortlisting or matching.
            ensure_design(conn, resume_id, settings, accepted_upload=True)
            conn.commit()
            candidate = jev.candidate_state(parsed.model.cv)
        else:
            candidate = jev.candidate_state(load_yaml(row["rendercv_yaml"]).cv)
        return await match_resume(conn, claim, candidate, settings)
    except LeaseLost:
        raise
    except Exception:
        log.exception("Matching résumé %s failed", resume_id)
        fail(
            conn,
            claim,
            "Matching was interrupted. Retry to continue from the saved results.",
        )
        return None
    finally:
        conn.close()


async def match_resume(
    conn: sqlite3.Connection,
    claim: Claim,
    candidate: dict[str, Any],
    settings: Settings,
) -> float | None:
    resume_id = claim.resume_id
    row = conn.execute("SELECT * FROM resumes WHERE id = ?", (resume_id,)).fetchone()
    revision, generation = row["candidate_revision"], row["work_generation"]
    old_stats = json.loads(row["search_stats"]) if row["search_stats"] else {}
    if row["planned_generation"] == generation and (
        old_stats.get("questions_version") != jev.QUESTIONS_VERSION
        or old_stats.get("scoring_model") != settings.jev_model
    ):
        # A deploy/configuration change cannot silently skip pending older work.
        # Keep the old results available and build a new versioned plan.
        generation += 1
        begin(conn, claim)
        conn.execute(
            "UPDATE resumes SET work_generation = ? WHERE id = ?",
            (generation, resume_id),
        )
        conn.commit()
    if row["planned_generation"] != generation:
        roles, stats = await asyncio.to_thread(
            shortlist_with_diagnostics,
            settings.jobs_db,
            candidate,
            settings.match_limit,
        )
        stats = stats | {
            "retrieval_limit": settings.match_limit,
            "candidate_location_known": bool(candidate.get("location")),
            "effective_location": candidate.get("location"),
            "location_source": candidate.get("location_source", "resume"),
            "questions_version": jev.QUESTIONS_VERSION,
            "scoring_model": settings.jev_model,
        }
        begin(conn, claim)
        conn.execute(
            "UPDATE resumes SET search_stats = ? WHERE id = ?",
            (json.dumps(stats), resume_id),
        )
        if not stats["catalog_total"]:
            conn.execute(
                "UPDATE resumes SET status = 'waiting_for_jobs', error = NULL WHERE id = ?",
                (resume_id,),
            )
            conn.commit()
            return settings.catalog_retry_seconds
        reused = False
        for role in roles:
            role = role | {
                "scoring_model": settings.jev_model,
                "questions_version": jev.QUESTIONS_VERSION,
            }
            key = job_key(role)
            # Unchanged results can be reused across catalog refreshes. The source
            # identity, content hash, candidate revision and question version agree.
            existing = conn.execute(
                """
                SELECT 1 FROM matches WHERE resume_id = ? AND candidate_revision = ?
                    AND job_key = ? AND questions = ?
            """,
                (resume_id, revision, key, jev.QUESTIONS_VERSION),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO resume_work (resume_id, job_key, candidate_revision, questions,
                    generation, job_snapshot, state, attempts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (resume_id, candidate_revision, job_key, questions) DO UPDATE SET
                    generation = excluded.generation, state = excluded.state, error = NULL
            """,
                (
                    resume_id,
                    key,
                    revision,
                    jev.QUESTIONS_VERSION,
                    generation,
                    snapshot(role),
                    "succeeded" if existing else "pending",
                    1 if existing else 0,
                ),
            )
            if existing:
                reused = True
                conn.execute(
                    """
                    UPDATE matches SET generation = ? WHERE resume_id = ? AND candidate_revision = ?
                        AND job_key = ? AND questions = ?
                """,
                    (generation, resume_id, revision, key, jev.QUESTIONS_VERSION),
                )
        conn.execute(
            """
            UPDATE resumes SET planned_generation = ?, status = 'matching', error = NULL
            WHERE id = ?
        """,
            (generation, resume_id),
        )
        if reused:
            publish_generation(conn, resume_id, generation)
        refresh_progress(conn, resume_id)
        conn.commit()
    else:
        begin(conn, claim)
        conn.execute(
            "UPDATE resumes SET status = 'matching', error = NULL WHERE id = ?",
            (resume_id,),
        )
        conn.commit()

    pending = conn.execute(
        """
        SELECT job_key, job_snapshot FROM resume_work WHERE resume_id = ? AND candidate_revision = ?
            AND generation = ? AND questions = ? AND state = 'pending'
    """,
        (resume_id, revision, generation, jev.QUESTIONS_VERSION),
    ).fetchall()
    roles = [json.loads(work["job_snapshot"]) for work in pending]
    if roles:
        async with jev.make_client(settings) as client:
            async for role, outcome in jev.score_jobs(
                client, candidate, roles, concurrency=settings.match_concurrency
            ):
                begin(conn, claim)
                key = job_key(dict(role))
                success = isinstance(outcome, jev.JobScore)
                if success:
                    save_score(
                        conn, resume_id, revision, generation, dict(role), outcome
                    )
                    publish_generation(conn, resume_id, generation)
                conn.execute(
                    """
                    UPDATE resume_work SET state = ?, attempts = attempts + 1, error = ?
                    WHERE resume_id = ? AND candidate_revision = ? AND job_key = ? AND questions = ?
                """,
                    (
                        "succeeded" if success else "failed",
                        None if success else "Scoring request failed. Retry this job.",
                        resume_id,
                        revision,
                        key,
                        jev.QUESTIONS_VERSION,
                    ),
                )
                refresh_progress(conn, resume_id)
                conn.commit()

    begin(conn, claim)
    counts = refresh_progress(conn, resume_id)
    pending_count = counts["total"] - counts["succeeded"] - counts["failed"]
    if pending_count:
        conn.rollback()
        raise RuntimeError(
            f"{pending_count} roles remain pending; processing did not complete"
        )
    if not counts["total"]:
        publish_generation(conn, resume_id, generation)
    status = (
        "partial"
        if counts["failed"] and counts["succeeded"]
        else "failed"
        if counts["failed"]
        else "done"
    )
    error = (
        f"{counts['failed']} scoring requests failed. Retry to finish these roles."
        if counts["failed"]
        else None
    )
    conn.execute(
        "UPDATE resumes SET status = ?, error = ? WHERE id = ?",
        (status, error, resume_id),
    )
    conn.commit()
    return None


def save_score(
    conn: sqlite3.Connection,
    resume_id: str,
    revision: int,
    generation: int,
    role: dict[str, Any],
    score: jev.JobScore,
) -> None:
    """Caller commits the result and work checkpoint in the same fenced transaction."""
    conn.execute(
        """
        INSERT INTO matches (resume_id, job_key, candidate_revision, generation, job_id, job_hash,
            questions, job_snapshot, provider, board, source_id, score, fit, interview, must_haves,
            location, seniority, locations, input_tokens, scored_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            resume_id,
            job_key(role),
            revision,
            generation,
            score.job_id,
            score.job_hash,
            jev.QUESTIONS_VERSION,
            snapshot(role),
            role["provider"],
            role["board"],
            role["source_id"],
            score.score,
            score.fit,
            score.interview,
            score.must_haves,
            score.location,
            score.seniority,
            json.dumps(role["locations"]),
            score.input_tokens,
            datetime.now(UTC).isoformat(timespec="seconds"),
        ),
    )
    conn.execute(
        "UPDATE resumes SET match_cost_usd = match_cost_usd + ? WHERE id = ?",
        (
            score.input_tokens * jev.PRICE_PER_MTOK / 1e6,
            resume_id,
        ),
    )


def fail(conn: sqlite3.Connection, claim: Claim, message: str) -> None:
    conn.rollback()
    begin(conn, claim)
    conn.execute(
        "UPDATE resumes SET status = 'failed', error = ? WHERE id = ?",
        (message, claim.resume_id),
    )
    conn.commit()


def publish_generation(
    conn: sqlite3.Connection, resume_id: str, generation: int
) -> None:
    conn.execute(
        "UPDATE resumes SET current_generation = ?, results_context = search_stats WHERE id = ?",
        (generation, resume_id),
    )
