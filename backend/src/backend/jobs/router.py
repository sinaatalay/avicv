"""Read-only job search over jobs.db.

Until the first `jobs-ingest` run has created the file, both endpoints answer with
empty results (`synced: false`) instead of an error.
"""

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..db import connect
from .store import source_health

router = APIRouter(prefix="/jobs", tags=["jobs"])

SettingsDep = Annotated[Settings, Depends(get_settings)]
Geo = Literal["us_ca", "remote_unspecified", "other", "unknown", "any"]

_SUMMARY_COLUMNS = (
    "j.id, j.provider, j.board, j.last_seen, j.company, j.title, j.locations_text, j.geo_bucket, j.workplace_type, "
    "j.employment_type, j.posted_at, j.apply_url, j.source_url, j.salary_min, j.salary_max, "
    "j.salary_currency, j.salary_interval"
)
_WORD = re.compile(r"[\w+#.]+")


class JobSummary(BaseModel):
    id: int
    provider: str
    company: str | None
    title: str
    locations: str | None
    geo_bucket: str | None
    workplace_type: str | None
    employment_type: str | None
    posted_at: str | None  # as the provider gave it
    url: str | None  # where to apply, else the posting
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_interval: str | None
    source_status: str
    source_verified_at: str | None
    source_stale: bool


class JobSearch(BaseModel):
    synced: bool  # False until a jobs-ingest run has finished
    total: int  # open jobs matching `q` and `geo`
    jobs: list[JobSummary]


class LastRun(BaseModel):
    status: str  # running | ok | partial | failed | abandoned
    started_at: str
    finished_at: str | None


class SourceHealth(BaseModel):
    provider: str
    board: str
    status: str
    configured: bool
    last_attempt_at: str | None
    last_verified_at: str | None
    stale: bool


class JobStats(BaseModel):
    synced: bool
    open_jobs: int
    by_provider: dict[str, int]
    by_geo: dict[str, int]
    last_run: LastRun | None
    last_success_at: str | None
    sources: list[SourceHealth] = Field(default_factory=list)
    stale_sources: int = 0


def _open(settings: Settings) -> sqlite3.Connection | None:
    return (
        connect(settings.jobs_db, readonly=True) if settings.jobs_db.exists() else None
    )


def _synced(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM ingest_runs WHERE status IN ('ok', 'partial') "
        "AND finished_at IS NOT NULL LIMIT 1"
    ).fetchone()
    return row is not None


def _summary(
    row: sqlite3.Row, health: dict[tuple[str, str], dict[str, Any]], stale_before: str
) -> JobSummary:
    source = health.get((row["provider"], row["board"]), {})
    return JobSummary(
        id=row["id"],
        provider=row["provider"],
        company=row["company"],
        title=row["title"],
        locations=row["locations_text"],
        geo_bucket=row["geo_bucket"],
        workplace_type=row["workplace_type"],
        employment_type=row["employment_type"],
        posted_at=row["posted_at"],
        url=row["apply_url"] or row["source_url"],
        salary_min=row["salary_min"],
        salary_max=row["salary_max"],
        salary_currency=row["salary_currency"],
        salary_interval=row["salary_interval"],
        source_status=source.get("status", "unknown"),
        source_verified_at=row["last_seen"]
        if row["provider"] == "himalayas"
        else source.get("last_verified_at"),
        source_stale=source.get("stale", True)
        or (row["provider"] == "himalayas" and row["last_seen"] < stale_before),
    )


def _as_phrases(q: str) -> str:
    """Each word of `q` as a quoted phrase, for input that isn't valid FTS5 syntax."""
    return " ".join('"' + word.replace('"', '""') + '"' for word in _WORD.findall(q))


def _search(
    conn: sqlite3.Connection, q: str, where: str, params: dict[str, Any]
) -> tuple[int, list[sqlite3.Row]]:
    source = (
        "FROM jobs_fts JOIN jobs AS j ON j.id = jobs_fts.rowid "
        f"WHERE jobs_fts MATCH :q AND {where}"
    )
    attempts = [q, _as_phrases(q)]  # FTS5 syntax first (AND, OR, "phrases", title:)
    for match in attempts:
        if not match:
            break
        try:
            total = conn.execute(
                f"SELECT count(*) {source}", params | {"q": match}
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT {_SUMMARY_COLUMNS}, bm25(jobs_fts, 4.0, 0.5, 0.0, 1.0) AS rank "
                f"{source} ORDER BY rank LIMIT :limit",
                params | {"q": match},
            ).fetchall()
        except sqlite3.OperationalError:  # e.g. "fts5: syntax error near ..."
            continue
        return total, rows
    return 0, []


def search_catalog(
    settings: SettingsDep,
    q: Annotated[str | None, Query(max_length=500, description="FTS5 query")] = None,
    geo: Geo = "us_ca",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> JobSearch:
    """Open jobs, best match first, or newest first without `q`. No descriptions."""
    conn = _open(settings)
    if conn is None:
        return JobSearch(synced=False, total=0, jobs=[])
    try:
        where = "j.closed_at IS NULL" + (
            "" if geo == "any" else " AND j.geo_bucket = :geo"
        )
        params: dict[str, Any] = {"limit": limit, "geo": geo}
        if q and q.strip():
            total, rows = _search(conn, q.strip(), where, params)
        else:
            total = conn.execute(
                f"SELECT count(*) FROM jobs AS j WHERE {where}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT {_SUMMARY_COLUMNS} FROM jobs AS j WHERE {where} "
                "ORDER BY j.first_seen DESC, j.id DESC LIMIT :limit",
                params,
            ).fetchall()
        freshness_hours = max(24, settings.jobs_refresh_hours * 2)
        health = source_health(conn, freshness_hours)
        cutoff = (datetime.now(UTC) - timedelta(hours=freshness_hours)).isoformat()
        return JobSearch(
            synced=_synced(conn),
            total=total,
            jobs=[_summary(r, health, cutoff) for r in rows],
        )
    except sqlite3.OperationalError:  # created but not initialized yet
        return JobSearch(synced=False, total=0, jobs=[])
    finally:
        conn.close()


def catalog_stats(settings: Settings) -> JobStats:
    """Open jobs by provider and by geography, and how the last sync went."""
    empty = JobStats(
        synced=False,
        open_jobs=0,
        by_provider={},
        by_geo={},
        last_run=None,
        last_success_at=None,
    )
    conn = _open(settings)
    if conn is None:
        return empty
    try:
        by_provider = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT provider, count(*) FROM jobs WHERE closed_at IS NULL GROUP BY provider"
            )
        }
        by_geo = {
            row[0] or "unknown": row[1]
            for row in conn.execute(
                "SELECT geo_bucket, count(*) FROM jobs WHERE closed_at IS NULL GROUP BY geo_bucket"
            )
        }
        last = conn.execute(
            "SELECT status, started_at, finished_at FROM ingest_runs ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        success = conn.execute(
            "SELECT max(finished_at) FROM ingest_runs WHERE status = 'ok'"
        ).fetchone()[0]
        synced = _synced(conn)
        sources = [
            SourceHealth(**state)
            for state in source_health(
                conn, max(24, settings.jobs_refresh_hours * 2)
            ).values()
        ]
    except sqlite3.OperationalError:
        return empty
    finally:
        conn.close()
    return JobStats(
        synced=synced,
        open_jobs=sum(by_provider.values()),
        by_provider=by_provider,
        by_geo=by_geo,
        last_run=LastRun(**dict(last)) if last else None,
        last_success_at=success,
        sources=sources,
        stale_sources=sum(source.stale for source in sources),
    )


@router.get("")
def search_jobs(
    settings: SettingsDep,
    q: Annotated[str | None, Query(max_length=500)] = None,
    geo: Geo = "us_ca",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> JobSearch:
    return search_catalog(settings, q=q, geo=geo, limit=limit)


@router.get("/stats")
def job_stats(settings: SettingsDep) -> JobStats:
    return catalog_stats(settings)
