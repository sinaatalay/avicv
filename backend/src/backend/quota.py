"""Shared, transactional admission budgets for REST and MCP mutations."""

import sqlite3
import time
from typing import TYPE_CHECKING

from fastapi import HTTPException

from .config import Settings

if TYPE_CHECKING:
    from .auth import Principal


def initialize(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS usage_budgets (
        owner TEXT NOT NULL, action TEXT NOT NULL, day INTEGER NOT NULL,
        used INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(owner, action, day)
    )""")


def check_quota(
    conn: sqlite3.Connection, settings: Settings, principal: Principal, action: str
) -> None:
    """Call in the mutation's write transaction, after checking for existing work."""
    initialize(conn)
    limits = {
        "upload": settings.uploads_per_day,
        "tailor": settings.tailors_per_day,
        "chat": settings.chats_per_day,
        "retry": settings.retries_per_day,
        "rematch": settings.retries_per_day,
        "upload_ticket": settings.uploads_per_day * 3,
    }
    limit = limits[action]
    day = int(time.time() // 86400)
    for owner, maximum in (
        (principal.workspace_id, limit),
        ("*", settings.global_actions_per_day),
    ):
        current = conn.execute(
            "SELECT used FROM usage_budgets WHERE owner=? AND action=? AND day=?",
            (owner, action, day),
        ).fetchone()
        if current and current[0] >= maximum:
            raise HTTPException(
                429,
                "Today's usage limit has been reached. Please try again tomorrow.",
                headers={
                    "Retry-After": str(max(1, (day + 1) * 86400 - int(time.time())))
                },
            )
        conn.execute(
            "INSERT INTO usage_budgets(owner, action, day, used) VALUES(?,?,?,1) "
            "ON CONFLICT(owner,action,day) DO UPDATE SET used=used+1",
            (owner, action, day),
        )
    conn.execute("DELETE FROM usage_budgets WHERE day < ?", (day - 2,))
