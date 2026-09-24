from __future__ import annotations

import asyncio
import sys
import warnings
from datetime import UTC, datetime, timedelta

from db_fixtures import admin_url, fresh_db_url, migrated_db_url, sync_engine  # noqa: F401

from app.oee import CounterSample, ShiftDef, ShiftPlan, Window

if sys.platform == "win32":  # psycopg's async mode cannot use the default Proactor event loop
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

BASE = datetime(2026, 1, 5, tzinfo=UTC)

PLAN = ShiftPlan(
    shifts=(
        ShiftDef("matin", 5 * 3600, 13 * 3600),
        ShiftDef("apres-midi", 13 * 3600, 21 * 3600),
        ShiftDef("nuit", 21 * 3600, 5 * 3600),
    ),
    day_start_s=5 * 3600,
)

# Cell states, as in CLAUDE.md
PRODUCING, PLANNED_STOP, FAULT, SETUP, STARVED, BLOCKED, MANUAL = 1, 2, 3, 4, 5, 6, 7


def t(h: int, m: int = 0, s: float = 0, day: int = 0) -> datetime:
    """Simulated instant on 2026-01-05 (+ ``day``), UTC."""
    return BASE + timedelta(days=day, hours=h, minutes=m, seconds=s)


def win(start: datetime, end: datetime, label: str = "w") -> Window:
    return Window(label, start, end)


def sample(ts: datetime, good: int, scrap: int = 0, tct: float = 55.0) -> CounterSample:
    return CounterSample(ts, good, scrap, tct)
