"""`jobs-ingest`: pull every board in `boards.toml`, and the Himalayas searches in
`himalayas.toml`, into `jobs.db`.

    uv run jobs-ingest           # skips if the last good run is newer than JOBS_REFRESH_HOURS
    uv run jobs-ingest --force   # runs regardless
    uv run jobs-ingest --loop    # stays up and checks every 15 minutes (`bun run dev`)

A file lock next to the database keeps runs from overlapping, so this is also safe to
start from cron, launchd or a systemd timer.
"""

import argparse
import asyncio
import fcntl
import json
import logging
import signal
import sqlite3
import time
import tomllib
from collections import Counter, defaultdict
from collections.abc import Coroutine, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2 as httpx

from ..config import get_settings
from . import normalize, store
from .http import Fetcher
from .providers import (
    BoardFetch,
    Item,
    fetch_ashby,
    fetch_greenhouse,
    fetch_himalayas,
    fetch_lever,
    fetch_smartrecruiters,
)

log = logging.getLogger(__name__)

HERE = Path(__file__).parent
PROVIDERS = ("greenhouse", "ashby", "lever", "smartrecruiters", "himalayas")
KEEP_BOARD_LOGS_DAYS = 30


@dataclass
class Options:
    db: Path
    boards: Path = HERE / "boards.toml"
    himalayas: Path | None = HERE / "himalayas.toml"  # None: skip Himalayas
    only: frozenset[str] = field(default_factory=frozenset)  # empty: every provider
    force: bool = False
    min_interval_hours: float = 6.0
    sr_detail_cap: int = 50
    budget_seconds: float = 540.0
    conditional: bool = True
    himalayas_ttl_days: float = 7.0
    trigger: str = "cli"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@contextmanager
def run_lock(db: Path) -> Generator[bool]:
    """True while this process holds the lock next to `db`, False if another one does.
    The OS releases it if the process dies."""
    db.parent.mkdir(parents=True, exist_ok=True)
    with db.with_name(db.name + ".lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


async def guarded(
    fetch: Coroutine[Any, Any, BoardFetch],
    provider: str,
    board: str,
    company: str,
    unit: str = "",
) -> BoardFetch:
    """A bad payload (KeyError, schema drift, ...) fails its own board, not the run."""
    try:
        return await fetch
    except Exception as e:  # noqa: BLE001
        return BoardFetch(provider, board, company, unit=unit).fail(e)


async def ingest(
    opts: Options, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    """One run. Call through `run_once`, which holds the lock."""
    boards: list[dict[str, Any]] = tomllib.loads(opts.boards.read_text())["board"]
    if opts.only:
        boards = [b for b in boards if b["provider"] in opts.only]
    use_himalayas = opts.himalayas is not None and (
        not opts.only or "himalayas" in opts.only
    )
    searches: dict[str, Any] = (
        tomllib.loads(opts.himalayas.read_text())
        if opts.himalayas and use_himalayas
        else {}
    )

    conn = store.connect(opts.db)
    try:
        last = store.last_success_started_at(conn)
        if last and not opts.force:
            age = datetime.now(UTC) - datetime.fromisoformat(last)
            if age < timedelta(hours=opts.min_interval_hours):
                hours = age.total_seconds() / 3600
                return {
                    "skipped": f"last good run started {hours:.1f} h ago (< {opts.min_interval_hours:g} h)"
                }
        return await _run(conn, opts, boards, searches, transport)
    finally:
        conn.close()


async def _run(
    conn: sqlite3.Connection,
    opts: Options,
    boards: list[dict[str, Any]],
    searches: dict[str, Any],
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
    now = now_iso()
    started = time.monotonic()
    run_id = store.start_run(conn, now, opts.trigger)
    by_key = {(b["provider"], b["id"]): b for b in boards}
    stored_sr = {
        b["id"]: store.stored_raw(conn, "smartrecruiters", b["id"])
        for b in boards
        if b["provider"] == "smartrecruiters"
    }
    validators = (
        store.load_validators(conn, normalize.NORMALIZER_VERSION)
        if opts.conditional
        else {}
    )
    totals: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    errors: list[dict[str, str]] = []

    def finish_board(bf: BoardFetch) -> None:
        """Normalize and store one board's fetch (runs in a worker thread)."""
        board = by_key[(bf.provider, bf.board)]
        normalizer = normalize.NORMALIZERS[bf.provider]

        def build(raw: Any, chash: str) -> normalize.Record | None:
            record = normalizer(raw, board)
            return (
                None
                if record is None
                else normalize.to_row(bf.provider, bf.board, raw, record, chash)
            )

        if bf.not_modified:
            open_jobs = store.mark_not_modified(conn, bf.provider, bf.board, now)
            applied = store.Applied(
                status="not_modified", seen=open_jobs, unchanged=open_jobs
            )
        else:
            applied = store.apply_board(
                conn, bf.provider, bf.board, bf.items, build, now, complete=bf.complete
            )
            if bf.status == "failed":
                store.mark_failed(conn, bf.provider, bf.board, now)
            elif bf.complete and applied.status == "ok" and bf.validators:
                # only now that the data is committed; a suspect listing is re-fetched
                store.save_validators(
                    conn, bf.validators, normalize.NORMALIZER_VERSION, now
                )
        status = "failed" if bf.status == "failed" else applied.status
        _count(totals, statuses, status, applied, bf.detail_calls)
        if bf.error:
            errors.append(
                {"provider": bf.provider, "board": bf.board, "error": bf.error}
            )
        store.log_board(conn, run_id, _board_log(bf, status, applied))

    fetcher = Fetcher(
        max_concurrency=8,
        per_host=2,
        min_gap=0.25,
        host_limits={"himalayas.app": 1},
        host_gaps={"himalayas.app": 1.0},
        deadline=time.monotonic() + opts.budget_seconds,
        transport=transport,
    )
    himalayas_jobs: dict[str, Item] = {}
    async with fetcher as f:
        tasks = [_fetch(f, b, validators, stored_sr, opts) for b in boards]
        tasks += [
            guarded(
                fetch_himalayas(f, query, country, searches.get("pages", 3)),
                "himalayas",
                "*",
                "Himalayas",
                unit=f"q={query} country={country}",
            )
            for query in searches.get("queries", [])
            for country in searches.get("countries", [])
        ]
        for next_done in asyncio.as_completed(tasks):
            bf = await next_done
            if bf.provider == "himalayas":
                for item in bf.items:
                    himalayas_jobs.setdefault(item[0], item)
                statuses["search_failed" if bf.status == "failed" else "search_ok"] += 1
                if bf.error:
                    errors.append(
                        {"provider": "himalayas", "unit": bf.unit, "error": bf.error}
                    )
                store.log_board(
                    conn, run_id, _board_log(bf, bf.status, store.Applied("partial"))
                )
                continue
            try:
                await asyncio.to_thread(finish_board, bf)
            except Exception as e:  # noqa: BLE001 - e.g. a normalizer bug on one board
                statuses["failed"] += 1
                errors.append(
                    {
                        "provider": bf.provider,
                        "board": bf.board,
                        "error": f"store: {e!r}",
                    }
                )
        http = f.totals()

    if searches:
        _store_himalayas(conn, himalayas_jobs, now, opts, totals)

    failed = statuses["failed"]
    status = "ok" if failed == 0 else "failed" if failed >= len(boards) else "partial"
    store.finish_run(
        conn,
        run_id,
        {
            "finished_at": now_iso(),
            "status": status,
            "boards_total": len(boards),
            "boards_ok": statuses["ok"] + statuses["not_modified"],
            "boards_partial": statuses["partial"] + statuses["suspect"],
            "boards_failed": failed,
            "jobs_seen": totals["seen"],
            "jobs_new": totals["new"],
            "jobs_changed": totals["changed"],
            "jobs_unchanged": totals["unchanged"],
            "jobs_closed": totals["closed"],
            "jobs_reopened": totals["reopened"],
            "requests": http["requests"],
            "retries": http["retries"],
            "bytes_wire": http["bytes_wire"],
            "bytes_decoded": http["bytes_decoded"],
            "detail_calls": totals["detail_calls"],
            "wall_seconds": round(time.monotonic() - started, 2),
            "http_json": json.dumps(http["by_host"]),
            "errors_json": json.dumps(errors),
        },
    )
    cutoff = datetime.now(UTC) - timedelta(days=KEEP_BOARD_LOGS_DAYS)
    store.prune_logs(conn, cutoff.isoformat(timespec="seconds"))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("PRAGMA optimize")
    return {
        "run_id": run_id,
        "status": status,
        "wall_seconds": round(time.monotonic() - started, 2),
        "boards": dict(statuses),
        **{f"jobs_{k}": v for k, v in totals.items() if k != "detail_calls"},
        "detail_calls": totals["detail_calls"],
        "requests": http["requests"],
        "retries": http["retries"],
        "mb_wire": round(http["bytes_wire"] / 1e6, 2),
        "mb_decoded": round(http["bytes_decoded"] / 1e6, 2),
        "errors": errors[:20],
    }


def _fetch(
    f: Fetcher,
    board: dict[str, Any],
    validators: dict[str, str],
    stored_sr: dict[str, dict[str, Any]],
    opts: Options,
) -> Coroutine[Any, Any, BoardFetch]:
    match board["provider"]:
        case "greenhouse":
            fetch = fetch_greenhouse(f, board, validators)
        case "ashby":
            fetch = fetch_ashby(f, board, validators)
        case "lever":
            fetch = fetch_lever(f, board, validators)
        case "smartrecruiters":
            fetch = fetch_smartrecruiters(
                f, board, stored_sr[board["id"]], opts.sr_detail_cap
            )
        case other:
            msg = f"unknown provider {other!r} in {opts.boards}"
            raise ValueError(msg)
    return guarded(fetch, board["provider"], board["id"], board["company"])


def _store_himalayas(
    conn: sqlite3.Connection,
    jobs: dict[str, Item],
    now: str,
    opts: Options,
    totals: Counter[str],
) -> None:
    """Search results are grouped by employer and never close jobs by absence; jobs close
    once expired or unseen for `himalayas_ttl_days`."""
    by_slug: defaultdict[str, list[Item]] = defaultdict(list)
    for item in jobs.values():
        by_slug[item[1].get("companySlug") or "unknown"].append(item)
    for slug, items in by_slug.items():

        def build(raw: Any, chash: str, slug: str = slug) -> normalize.Record:
            return normalize.to_row(
                "himalayas", slug, raw, normalize.himalayas(raw), chash
            )

        applied = store.apply_board(
            conn, "himalayas", slug, items, build, now, complete=False, track_seen=True
        )
        for name in ("seen", "new", "changed", "unchanged", "reopened"):
            totals[name] += getattr(applied, name)
    unseen_since = datetime.now(UTC) - timedelta(days=opts.himalayas_ttl_days)
    totals["closed"] += store.expire_unseen(
        conn, "himalayas", now, unseen_since.isoformat(timespec="seconds")
    )


def _count(
    totals: Counter[str],
    statuses: Counter[str],
    status: str,
    applied: store.Applied,
    details: int,
) -> None:
    statuses[status] += 1
    for name in ("seen", "new", "changed", "unchanged", "closed", "reopened"):
        totals[name] += getattr(applied, name)
    totals["detail_calls"] += details


def _board_log(bf: BoardFetch, status: str, applied: store.Applied) -> dict[str, Any]:
    return {
        "provider": bf.provider,
        "board": bf.board,
        "unit": bf.unit,
        "status": status,
        "complete": int(bf.complete),
        "http_status": bf.http_status,
        "pages": bf.pages,
        "requests": bf.requests,
        "bytes_wire": bf.bytes_wire,
        "reported_total": bf.reported_total,
        "jobs_seen": applied.seen if bf.provider != "himalayas" else len(bf.items),
        "jobs_new": applied.new,
        "jobs_changed": applied.changed,
        "jobs_unchanged": applied.unchanged,
        "jobs_closed": applied.closed,
        "jobs_reopened": applied.reopened,
        "detail_calls": bf.detail_calls,
        "detail_reused": bf.detail_reused,
        "detail_deferred": bf.detail_deferred,
        "detail_seconds": round(bf.detail_seconds, 3),
        "duration_ms": int((time.monotonic() - bf.started) * 1000),
        "error": bf.error,
    }


def run_once(
    opts: Options, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    """One guarded run: skipped if another process holds the lock."""
    with run_lock(opts.db) as locked:
        if not locked:
            return {"skipped": "another jobs-ingest run holds the lock"}
        try:
            return asyncio.run(ingest(opts, transport))
        except BaseException:
            conn = store.connect(opts.db)
            try:
                store.fail_running(conn, now_iso())
            finally:
                conn.close()
            raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="jobs-ingest", description="Pull job postings into jobs.db."
    )
    parser.add_argument(
        "--force", action="store_true", help="run even if the last run is recent"
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="stay running and check every --loop-minutes",
    )
    parser.add_argument("--loop-minutes", type=float, default=15.0)
    parser.add_argument(
        "--min-interval-hours",
        type=float,
        default=settings.jobs_refresh_hours,
        help="skip if the last good run started more recently (default: JOBS_REFRESH_HOURS)",
    )
    parser.add_argument("--db", type=Path, default=settings.jobs_db)
    parser.add_argument("--boards", type=Path, default=HERE / "boards.toml")
    parser.add_argument("--himalayas", type=Path, default=HERE / "himalayas.toml")
    parser.add_argument("--no-himalayas", action="store_true")
    parser.add_argument(
        "--only",
        action="append",
        choices=PROVIDERS,
        help="limit to a provider (repeatable)",
    )
    parser.add_argument(
        "--sr-detail-cap",
        type=int,
        default=50,
        help="SmartRecruiters detail calls per company per run (descriptions); 2000 backfills",
    )
    parser.add_argument("--budget-seconds", type=float, default=540.0)
    parser.add_argument(
        "--no-conditional", action="store_true", help="don't send If-None-Match (ETags)"
    )
    parser.add_argument("--himalayas-ttl-days", type=float, default=7.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx2").setLevel(
        logging.WARNING
    )  # one INFO line per request otherwise
    # Stop on SIGTERM (process managers) like on Ctrl-C: a run in flight is logged as failed
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    opts = Options(
        db=args.db,
        boards=args.boards,
        himalayas=None if args.no_himalayas else args.himalayas,
        only=frozenset(args.only or ()),
        force=args.force,
        min_interval_hours=args.min_interval_hours,
        sr_detail_cap=args.sr_detail_cap,
        budget_seconds=args.budget_seconds,
        conditional=not args.no_conditional,
        himalayas_ttl_days=args.himalayas_ttl_days,
        trigger="loop" if args.loop else "cli",
    )
    if not args.loop:
        try:
            print(json.dumps(run_once(opts), indent=2))
        except KeyboardInterrupt:
            log.info("interrupted")
            raise SystemExit(130) from None
        return
    log.info(
        "checking every %g min; a run starts when the last good one is %g h old",
        args.loop_minutes,
        opts.min_interval_hours,
    )
    try:
        while True:
            try:
                summary = run_once(opts)
            except Exception:
                log.exception("run failed")
            else:
                log.info("%s", json.dumps(summary))
            opts.force = False  # --force applies to the first check only
            time.sleep(args.loop_minutes * 60)
    except KeyboardInterrupt:
        log.info("stopped")
