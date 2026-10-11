"""Compare saved scoring inputs with the catalog without replacing snapshot data."""

import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, TypedDict, cast

from ..config import Settings
from ..db import connect
from ..jobs.store import source_health

log = logging.getLogger(__name__)
type Identity = tuple[str, str, str]
type CatalogStatus = Literal["open", "closed", "changed", "missing", "unverified"]
BATCH_SIZE = (
    200  # 600 bindings: also compatible with SQLite's older 999-variable limit.
)


class CatalogState(TypedDict):
    catalog_status: CatalogStatus
    source_status: str
    source_verified_at: str | None
    source_stale: bool


def _identity(item: dict[str, Any]) -> Identity | None:
    values = tuple(item.get(name) for name in ("provider", "board", "source_id"))
    if not all(isinstance(value, str) and value for value in values):
        return None
    return cast(Identity, values)


def _members(saved: sqlite3.Row) -> list[tuple[Identity, str]] | None:
    """Old snapshots can verify their representative; unrecorded members are not guessed."""
    try:
        snapshot = json.loads(saved["job_snapshot"])
    except TypeError, ValueError:
        return None
    if not isinstance(snapshot, dict):
        return None
    postings = snapshot.get("postings")
    if postings is None:
        postings = [
            {
                name: snapshot.get(name) or saved[name]
                for name in ("provider", "board", "source_id")
            }
            | {"content_hash": snapshot.get("content_hash") or saved["job_hash"]}
        ]
    if not isinstance(postings, list) or not postings:
        return None
    members: list[tuple[Identity, str]] = []
    for posting in postings:
        if not isinstance(posting, dict):
            return None
        identity, content_hash = _identity(posting), posting.get("content_hash")
        if identity is None or not isinstance(content_hash, str) or not content_hash:
            return None
        members.append((identity, content_hash))
    return members


def _catalog_snapshot(
    settings: Settings, identities: set[Identity], stale_after_hours: float
) -> tuple[dict[Identity, dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    if not identities or not settings.jobs_db.exists():
        return {}, {}
    conn: sqlite3.Connection | None = None
    try:
        conn = connect(settings.jobs_db, readonly=True)
        conn.execute(
            "BEGIN"
        )  # All member hashes and source health share a catalog revision.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        if (
            not {"provider", "board", "source_id", "content_hash", "closed_at"}
            <= columns
        ):
            return {}, {}
        # Historical fixtures/catalogs may not have freshness metadata yet.
        last_seen = "last_seen" if "last_seen" in columns else "NULL AS last_seen"
        records: dict[Identity, dict[str, Any]] = {}
        ordered = sorted(identities)
        for offset in range(0, len(ordered), BATCH_SIZE):
            batch = ordered[offset : offset + BATCH_SIZE]
            placeholders = ", ".join("(?, ?, ?)" for _ in batch)
            rows = conn.execute(
                f"SELECT provider, board, source_id, content_hash, closed_at, {last_seen} FROM jobs "
                f"WHERE (provider, board, source_id) IN (VALUES {placeholders})",
                [value for identity in batch for value in identity],
            )
            for row in rows:
                records[(row["provider"], row["board"], row["source_id"])] = dict(row)
        try:
            health = source_health(conn, stale_after_hours)
        except sqlite3.OperationalError:
            health = {}
        return records, health
    except sqlite3.DatabaseError as exc:
        # Historical results stay readable while their independent catalog is absent,
        # being initialized, unavailable or damaged. Their identity is never guessed.
        log.warning("Unable to inspect job catalog: %s", exc)
        return {}, {}
    finally:
        if conn is not None:
            conn.close()


def catalog_states(
    app_conn: sqlite3.Connection, resume_id: str, generation: int, settings: Settings
) -> dict[str, CatalogState]:
    """Freshness for every saved role before filtering and paginating its results.

    Missing membership wins over a closed/changed surviving member: only a fully
    verified group whose every posting closed is hidden as closed. A changed or
    closed secondary posting invalidates an otherwise unchanged representative.
    """
    saved = app_conn.execute(
        "SELECT job_key, job_snapshot, provider, board, source_id, job_hash FROM matches "
        "WHERE resume_id = ? AND generation = ?",
        (resume_id, generation),
    ).fetchall()
    groups = {row["job_key"]: _members(row) for row in saved}
    identities = {
        identity for members in groups.values() if members for identity, _ in members
    }
    freshness_hours = max(24, settings.jobs_refresh_hours * 2)
    cutoff = (datetime.now(UTC) - timedelta(hours=freshness_hours)).isoformat()
    current, health = _catalog_snapshot(settings, identities, freshness_hours)
    result: dict[str, CatalogState] = {}
    for job_key, members in groups.items():
        state: CatalogState = {
            "catalog_status": "unverified",
            "source_status": "unknown",
            "source_verified_at": None,
            "source_stale": True,
        }
        result[job_key] = state
        if not members:
            continue
        rows = [current.get(identity) for identity, _ in members]
        if any(row is None for row in rows):
            state["catalog_status"] = "missing"
            continue
        present = [row for row in rows if row is not None]
        if all(row["closed_at"] is not None for row in present):
            state["catalog_status"] = "closed"
        elif any(
            row["closed_at"] is not None or row["content_hash"] != chash
            for row, (_, chash) in zip(present, members, strict=True)
        ):
            state["catalog_status"] = "changed"
        else:
            state["catalog_status"] = "open"

        sources = [
            health.get((identity[0], identity[1]), {}) for identity, _ in members
        ]
        statuses = {source.get("status", "unknown") for source in sources}
        state["source_status"] = next(
            (
                status
                for status in (
                    "unconfigured",
                    "failed",
                    "suspect",
                    "partial",
                    "unknown",
                )
                if status in statuses
            ),
            "not_modified" if statuses == {"not_modified"} else "ok",
        )
        timestamps = [
            row["last_seen"]
            if identity[0] == "himalayas"
            else source.get("last_verified_at")
            for row, (identity, _), source in zip(
                present, members, sources, strict=True
            )
        ]
        known_timestamps = [
            timestamp
            for timestamp in timestamps
            if isinstance(timestamp, str) and timestamp
        ]
        if len(known_timestamps) == len(timestamps):
            state["source_verified_at"] = min(known_timestamps)
        state["source_stale"] = (
            any(source.get("stale", True) for source in sources)
            or len(known_timestamps) != len(timestamps)
            or any(timestamp < cutoff for timestamp in known_timestamps)
        )
    return result
