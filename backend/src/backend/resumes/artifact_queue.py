"""Independent leased worker lanes for design reconstruction and tailored PDFs.

Each lane has one active request across processes sharing app.db. Matching uses
its existing queue and never waits for either artifact lane.
"""

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass
from uuid import uuid4

from ..config import Settings
from ..db import connect_app
from .artifact_store import ArtifactKind, now_iso

log = logging.getLogger(__name__)


class ArtifactLeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class ArtifactClaim:
    kind: ArtifactKind
    target_id: str
    resume_id: str
    owner: str


def claim_artifact(settings: Settings, kind: ArtifactKind) -> ArtifactClaim | None:
    conn = connect_app(settings.app_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = time.time()
        conn.execute(
            """
            UPDATE artifact_queue SET state = 'pending', owner = NULL, lease_until = NULL
            WHERE kind = ? AND state = 'running' AND lease_until <= ?
        """,
            (kind, now),
        )
        if conn.execute(
            "SELECT 1 FROM artifact_queue WHERE kind = ? AND state = 'running' LIMIT 1",
            (kind,),
        ).fetchone():
            conn.commit()
            return None
        row = conn.execute(
            """
            SELECT target_id, resume_id FROM artifact_queue WHERE kind = ? AND state = 'pending' AND available_at <= ?
            ORDER BY available_at, target_id LIMIT 1
        """,
            (kind, now),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        claim = ArtifactClaim(kind, row["target_id"], row["resume_id"], uuid4().hex)
        conn.execute(
            """
            UPDATE artifact_queue SET state = 'running', owner = ?, lease_until = ?, attempts = attempts + 1
            WHERE kind = ? AND target_id = ?
        """,
            (claim.owner, now + settings.artifact_lease_seconds, kind, claim.target_id),
        )
        conn.commit()
        return claim
    finally:
        conn.close()


def artifact_fence(conn: sqlite3.Connection, claim: ArtifactClaim) -> None:
    if not conn.execute(
        """
        SELECT 1 FROM artifact_queue WHERE kind = ? AND target_id = ? AND state = 'running'
            AND owner = ? AND lease_until > ?
    """,
        (claim.kind, claim.target_id, claim.owner, time.time()),
    ).fetchone():
        raise ArtifactLeaseLost("The artifact lease expired or its résumé was deleted")


def begin_artifact(conn: sqlite3.Connection, claim: ArtifactClaim) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        artifact_fence(conn, claim)
    except BaseException:
        conn.rollback()
        raise


def release_artifact(
    settings: Settings, claim: ArtifactClaim, retry_after: float | None = None
) -> None:
    conn = connect_app(settings.app_db)
    try:
        with conn:
            conn.execute(
                """
                UPDATE artifact_queue SET state = ?, owner = NULL, lease_until = NULL, available_at = ?
                WHERE kind = ? AND target_id = ? AND owner = ? AND state = 'running'
            """,
                (
                    "done" if retry_after is None else "pending",
                    time.time() + (retry_after or 0),
                    claim.kind,
                    claim.target_id,
                    claim.owner,
                ),
            )
    finally:
        conn.close()


async def artifact_heartbeat(settings: Settings, claim: ArtifactClaim) -> None:
    while True:
        await asyncio.sleep(settings.artifact_lease_seconds / 3)
        conn = connect_app(settings.app_db)
        try:
            with conn:
                cursor = conn.execute(
                    """
                    UPDATE artifact_queue SET lease_until = ? WHERE kind = ? AND target_id = ? AND owner = ?
                        AND state = 'running' AND lease_until > ?
                """,
                    (
                        time.time() + settings.artifact_lease_seconds,
                        claim.kind,
                        claim.target_id,
                        claim.owner,
                        time.time(),
                    ),
                )
                if not cursor.rowcount:
                    raise ArtifactLeaseLost("Artifact worker lost its lease")
        finally:
            conn.close()


def fail_artifact(settings: Settings, claim: ArtifactClaim, message: str) -> None:
    conn = connect_app(settings.app_db)
    try:
        begin_artifact(conn, claim)
        table = "resume_designs" if claim.kind == "design" else "tailored_cvs"
        conn.execute(
            f"UPDATE {table} SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
            (message, now_iso(), claim.target_id),
        )
        conn.commit()
    finally:
        conn.close()


async def run_artifact_claim(settings: Settings, claim: ArtifactClaim) -> None:
    from .artifacts import process_artifact

    processing = asyncio.create_task(process_artifact(settings, claim))
    renewal = asyncio.create_task(artifact_heartbeat(settings, claim))
    try:
        async with asyncio.timeout(settings.artifact_timeout_seconds):
            finished, _ = await asyncio.wait(
                [processing, renewal], return_when=asyncio.FIRST_COMPLETED
            )
            if renewal in finished:
                await renewal
            retry_after = await processing
            release_artifact(settings, claim, retry_after)
    except asyncio.CancelledError:
        processing.cancel()
        await asyncio.gather(processing, return_exceptions=True)
        release_artifact(settings, claim, 0)
        raise
    except ArtifactLeaseLost:
        log.info("Artifact lease ended for %s", claim.target_id)
    except Exception:
        processing.cancel()
        await asyncio.gather(processing, return_exceptions=True)
        log.exception("Artifact %s failed", claim.target_id)
        try:
            fail_artifact(
                settings,
                claim,
                "We couldn't prepare this CV. Try Tailor CV again to continue from saved progress.",
            )
            release_artifact(settings, claim)
        except ArtifactLeaseLost:
            pass
    finally:
        processing.cancel()
        renewal.cancel()
        await asyncio.gather(processing, renewal, return_exceptions=True)


async def artifact_worker(settings: Settings, kind: ArtifactKind) -> None:
    while True:
        try:
            claim = claim_artifact(settings, kind)
            if claim:
                await run_artifact_claim(settings, claim)
            else:
                await asyncio.sleep(settings.artifact_poll_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Unable to poll %s artifact queue", kind)
            await asyncio.sleep(settings.artifact_poll_seconds)
