"""A durable, single-machine queue with one active résumé across API processes.

BEGIN IMMEDIATE makes claims atomic; an expiring lease recovers crashed workers.
Every processing write is fenced by the claim token, so an expired worker cannot
publish a late result after another worker has taken over.
"""

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass
from uuid import uuid4

from ..config import Settings
from ..db import connect_app

log = logging.getLogger(__name__)


class LeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class Claim:
    resume_id: str
    owner: str


def enqueue(
    conn: sqlite3.Connection, resume_id: str, *, available_at: float | None = None
) -> None:
    conn.execute(
        """
        INSERT INTO resume_queue (resume_id, state, available_at) VALUES (?, 'pending', ?)
        ON CONFLICT (resume_id) DO UPDATE SET state = 'pending', available_at = excluded.available_at,
            owner = NULL, lease_until = NULL
    """,
        (resume_id, time.time() if available_at is None else available_at),
    )


def recover_legacy(settings: Settings) -> None:
    conn = connect_app(settings.app_db)
    try:
        with conn:
            for row in conn.execute("""
                SELECT id, rendercv_yaml, upload_pdf FROM resumes WHERE status IN
                    ('queued', 'parsing', 'matching', 'waiting_for_jobs')
                    AND NOT EXISTS (SELECT 1 FROM resume_queue WHERE resume_id = resumes.id)
            """).fetchall():
                if row["rendercv_yaml"] or row["upload_pdf"]:
                    enqueue(conn, row["id"])
                else:
                    conn.execute(
                        "UPDATE resumes SET status = 'failed', error = ? WHERE id = ?",
                        (
                            "Processing was interrupted before this version stored uploads. Please upload the PDF again.",
                            row["id"],
                        ),
                    )
    finally:
        conn.close()


def claim_next(settings: Settings, *, now: float | None = None) -> Claim | None:
    now = time.time() if now is None else now
    conn = connect_app(settings.app_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE resume_queue SET state = 'pending', owner = NULL, lease_until = NULL
            WHERE state = 'running' AND lease_until <= ?
        """,
            (now,),
        )
        if conn.execute(
            "SELECT 1 FROM resume_queue WHERE state = 'running' LIMIT 1"
        ).fetchone():
            conn.commit()
            return None
        row = conn.execute(
            """
            SELECT resume_id FROM resume_queue WHERE state = 'pending' AND available_at <= ?
            ORDER BY available_at, resume_id LIMIT 1
        """,
            (now,),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        claim = Claim(row["resume_id"], uuid4().hex)
        conn.execute(
            """
            UPDATE resume_queue SET state = 'running', owner = ?, lease_until = ?, attempts = attempts + 1
            WHERE resume_id = ?
        """,
            (claim.owner, now + settings.queue_lease_seconds, claim.resume_id),
        )
        conn.commit()
        return claim
    finally:
        conn.close()


def fence(conn: sqlite3.Connection, claim: Claim) -> None:
    """Call inside a write transaction before committing any processing results."""
    row = conn.execute(
        """
        SELECT 1 FROM resume_queue WHERE resume_id = ? AND state = 'running'
            AND owner = ? AND lease_until > ?
    """,
        (claim.resume_id, claim.owner, time.time()),
    ).fetchone()
    if row is None:
        raise LeaseLost("Résumé worker lease no longer belongs to this task")


def release(
    settings: Settings, claim: Claim, *, retry_after: float | None = None
) -> None:
    conn = connect_app(settings.app_db)
    try:
        with conn:
            conn.execute(
                """
                UPDATE resume_queue SET state = ?, owner = NULL, lease_until = NULL, available_at = ?
                WHERE resume_id = ? AND owner = ? AND state = 'running'
            """,
                (
                    "done" if retry_after is None else "pending",
                    time.time() + (retry_after or 0),
                    claim.resume_id,
                    claim.owner,
                ),
            )
    finally:
        conn.close()


async def heartbeat(settings: Settings, claim: Claim) -> None:
    while True:
        await asyncio.sleep(settings.queue_lease_seconds / 3)
        conn = connect_app(settings.app_db)
        try:
            with conn:
                updated = conn.execute(
                    """
                    UPDATE resume_queue SET lease_until = ? WHERE resume_id = ? AND owner = ?
                        AND state = 'running' AND lease_until > ?
                """,
                    (
                        time.time() + settings.queue_lease_seconds,
                        claim.resume_id,
                        claim.owner,
                        time.time(),
                    ),
                )
                if not updated.rowcount:
                    raise LeaseLost("Résumé worker lost its lease")
        finally:
            conn.close()


async def run_claim(settings: Settings, claim: Claim) -> None:
    from .pipeline import process_resume

    processing = asyncio.create_task(process_resume(claim, settings))
    renewal = asyncio.create_task(heartbeat(settings, claim))
    try:
        async with asyncio.timeout(settings.processing_timeout_seconds):
            completed, _ = await asyncio.wait(
                [processing, renewal], return_when=asyncio.FIRST_COMPLETED
            )
            if renewal in completed:
                await renewal
            retry_after = await processing
            release(settings, claim, retry_after=retry_after)
    except asyncio.CancelledError:
        # Complete cancellation before making the work eligible for another worker.
        processing.cancel()
        await asyncio.gather(processing, return_exceptions=True)
        release(settings, claim, retry_after=0)
        raise
    except LeaseLost:
        log.info("Worker lease ended for résumé %s", claim.resume_id)
    except Exception:
        log.exception("Queue worker failed for résumé %s", claim.resume_id)
        conn = connect_app(settings.app_db)
        try:
            conn.execute("BEGIN IMMEDIATE")
            fence(conn, claim)
            conn.execute(
                "UPDATE resumes SET status = 'failed', error = ? WHERE id = ?",
                (
                    "Processing was interrupted. Retry to continue from the saved results.",
                    claim.resume_id,
                ),
            )
            conn.commit()
            release(settings, claim)
        except LeaseLost:
            conn.rollback()
        finally:
            conn.close()
    finally:
        processing.cancel()
        renewal.cancel()
        await asyncio.gather(processing, renewal, return_exceptions=True)


async def worker(settings: Settings) -> None:
    while True:
        try:
            claim = claim_next(settings)
            if claim:
                await run_claim(settings, claim)
            else:
                await asyncio.sleep(settings.queue_poll_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Unable to poll résumé queue")
            await asyncio.sleep(settings.queue_poll_seconds)
