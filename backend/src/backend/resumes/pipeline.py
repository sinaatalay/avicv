"""What happens after an upload: parse the PDF, shortlist jobs, then score each one with Jev.

Runs as a background task and records its progress in app.db, which the frontend polls.
"""

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
from ..matching.prefilter import shortlist
from .parse import parse_resume, preview

log = logging.getLogger(__name__)

PROGRESS_INTERVAL = 0.5  # seconds between progress writes while scoring


async def process_resume(resume_id: str, pdf: bytes, settings: Settings) -> None:
    conn = connect_app(settings.app_db)
    last_preview = {"at": 0.0, "value": ""}

    def show_progress(partial_yaml: str) -> None:
        if time.monotonic() - last_preview["at"] < PROGRESS_INTERVAL:
            return
        value = json.dumps(preview(partial_yaml))
        if value != last_preview["value"]:
            conn.execute(
                "UPDATE resumes SET parse_preview = ? WHERE id = ?", (value, resume_id)
            )
            conn.commit()
            last_preview["value"] = value
        last_preview["at"] = time.monotonic()

    try:
        try:
            parsed = await parse_resume(pdf, settings, on_text=show_progress)
        except Exception:
            log.exception("Parsing résumé %s failed", resume_id)
            fail(conn, resume_id, "We couldn't read a résumé from this PDF.")
            return
        conn.execute(
            "UPDATE resumes SET status = 'matching', rendercv_yaml = ?, parse_seconds = ?, parse_cost_usd = ? "
            "WHERE id = ?",
            (parsed.yaml, parsed.seconds, parsed.cost_usd, resume_id),
        )
        conn.commit()
        await match_resume(
            conn, resume_id, jev.candidate_state(parsed.model.cv), settings
        )
    except Exception:
        log.exception("Matching résumé %s failed", resume_id)
        fail(conn, resume_id, "Something went wrong while matching jobs.")
    finally:
        conn.close()


async def match_resume(
    conn: sqlite3.Connection,
    resume_id: str,
    candidate: dict[str, Any],
    settings: Settings,
) -> None:
    roles = await asyncio.to_thread(
        shortlist, settings.jobs_db, candidate, settings.match_limit
    )
    conn.execute(
        "UPDATE resumes SET match_total = ?, match_done = 0 WHERE id = ?",
        (len(roles), resume_id),
    )
    conn.commit()

    if roles:
        done = failed = tokens = 0
        batch: list[tuple[list[str], jev.JobScore]] = []
        last_write = time.monotonic()
        async with jev.make_client(settings) as client:
            async for role, outcome in jev.score_jobs(
                client, candidate, roles, concurrency=settings.match_concurrency
            ):
                done += 1
                if isinstance(outcome, jev.JobScore):
                    batch.append((role["locations"], outcome))
                    tokens += outcome.input_tokens
                else:
                    failed += 1
                if (
                    done == len(roles)
                    or time.monotonic() - last_write > PROGRESS_INTERVAL
                ):
                    save_scores(conn, resume_id, batch, done, tokens)
                    batch.clear()
                    last_write = time.monotonic()
        if failed == len(roles):
            raise RuntimeError(f"All {failed} Jev requests failed")

    conn.execute("UPDATE resumes SET status = 'done' WHERE id = ?", (resume_id,))
    conn.commit()


def save_scores(
    conn: sqlite3.Connection,
    resume_id: str,
    scores: list[tuple[list[str], jev.JobScore]],
    done: int,
    tokens: int,
) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    conn.executemany(
        """
        INSERT INTO matches (resume_id, job_id, job_hash, questions, score, fit, interview, must_haves, location,
                             seniority, locations, input_tokens, scored_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (resume_id, job_id, questions) DO UPDATE SET
            job_hash = excluded.job_hash, score = excluded.score, fit = excluded.fit,
            interview = excluded.interview, must_haves = excluded.must_haves, location = excluded.location,
            seniority = excluded.seniority, locations = excluded.locations, input_tokens = excluded.input_tokens,
            scored_at = excluded.scored_at
        """,
        [
            (
                resume_id,
                s.job_id,
                s.job_hash,
                jev.QUESTIONS_VERSION,
                s.score,
                s.fit,
                s.interview,
                s.must_haves,
                s.location,
                s.seniority,
                json.dumps(locations),
                s.input_tokens,
                now,
            )
            for locations, s in scores
        ],
    )
    conn.execute(
        "UPDATE resumes SET match_done = ?, match_cost_usd = ? WHERE id = ?",
        (done, tokens * jev.PRICE_PER_MTOK / 1e6, resume_id),
    )
    conn.commit()


def fail(conn: sqlite3.Connection, resume_id: str, message: str) -> None:
    conn.execute(
        "UPDATE resumes SET status = 'failed', error = ? WHERE id = ?",
        (message, resume_id),
    )
    conn.commit()
