"""Conservative saved-work counts and explicit product time assumptions.

The constants estimate manual effort; they are not measured elapsed time or
research-backed productivity claims. Only persisted successful work counts.
"""

import json
import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any, cast

from pydantic import BaseModel

from .links import valid_job_url

JOB_REVIEW_SECONDS = 30
CV_TAILORING_SECONDS = 600
type Identity = tuple[str, str, str]


class TimeSavings(BaseModel):
    jobs_reviewed: int
    cvs_tailored: int
    job_review_seconds: int = JOB_REVIEW_SECONDS
    cv_tailoring_seconds: int = CV_TAILORING_SECONDS
    estimated_seconds: int


def identity(values: Mapping[str, Any]) -> Identity | None:
    parts = [values.get(name) for name in ("provider", "board", "source_id")]
    if all(isinstance(part, str) and part.strip() for part in parts):
        return cast(Identity, tuple(parts))
    return None


def unique_saved_jobs(rows: Iterable[sqlite3.Row]) -> int:
    """Deduplicate versions and explicitly grouped postings, never numeric IDs.

    An immutable URL is a conservative legacy fallback. Rows with neither stable
    identity nor a usable saved URL cannot establish a unique job and are omitted.
    """
    parents: dict[Identity, Identity] = {}
    known_urls: set[str] = set()
    legacy_urls: set[str] = set()

    def representative(key: Identity) -> Identity:
        parents.setdefault(key, key)
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for row in rows:
        try:
            job = json.loads(row["job_snapshot"]) if row["job_snapshot"] else {}
        except ValueError, TypeError:
            job = {}
        if not isinstance(job, dict):
            job = {}
        stored = dict(row)
        primary = identity(stored) or identity(job)
        members = [primary] if primary else []
        postings = job.get("postings", [])
        if isinstance(postings, list):
            for posting in postings:
                if isinstance(posting, dict) and (member := identity(posting)):
                    members.append(member)
        urls = [
            url
            for name in ("source_url", "apply_url")
            if (url := valid_job_url(job.get(name)))
        ]
        if members:
            anchor = representative(members[0])
            for member in members[1:]:
                parents[representative(member)] = anchor
            known_urls.update(urls)
        elif urls:
            legacy_urls.add(urls[0])
    # Overlapping explicit groups remain one role even when a different posting
    # becomes the representative after a catalog refresh.
    return len({representative(key) for key in parents}) + len(legacy_urls - known_urls)


def time_savings(conn: sqlite3.Connection, resume_id: str) -> TimeSavings:
    jobs_reviewed = unique_saved_jobs(
        conn.execute(
            "SELECT provider, board, source_id, job_snapshot FROM matches WHERE resume_id = ?",
            (resume_id,),
        )
    )
    cvs_tailored = unique_saved_jobs(
        conn.execute(
            """
            SELECT job_snapshot FROM tailored_cvs WHERE resume_id = ? AND status = 'ready'
                AND substr(pdf_bytes, 1, 5) = X'255044462D'
        """,
            (resume_id,),
        )
    )
    return TimeSavings(
        jobs_reviewed=jobs_reviewed,
        cvs_tailored=cvs_tailored,
        estimated_seconds=jobs_reviewed * JOB_REVIEW_SECONDS
        + cvs_tailored * CV_TAILORING_SECONDS,
    )
