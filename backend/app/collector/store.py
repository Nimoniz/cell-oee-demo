"""Persistence of the collector: idempotent writes, and the state to resume from.

Every write is keyed by a natural key made of source timestamps, with ``ON CONFLICT DO NOTHING``:
delivering the same fact twice stores it once. A batch is one transaction, so a crash leaves
either all of it or none of it: an event and the stop it opens or closes never get separated.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.domain import STATION_OF_ORIGIN, CellState, suggest_cause

from .tracker import TrackerState
from .types import (
    ClosedGapOp,
    CloseGapOp,
    CloseStopOp,
    CounterSampleOp,
    Op,
    OpenGapOp,
    OpenStopOp,
    StateEventOp,
    WeldSampleOp,
)

log = logging.getLogger(__name__)

_SQL: dict[type, Any] = {
    StateEventOp: text(
        "INSERT INTO state_events (scope, ts, state, fault_code, stop_origin) "
        "VALUES (:scope, :ts, :state, :fault_code, :stop_origin) "
        "ON CONFLICT (scope, ts) DO NOTHING"
    ),
    OpenStopOp: text(
        "INSERT INTO stops (start_ts, state, origin_station, fault_code, suggested_category) "
        "VALUES (:start_ts, :state, :origin_station, :fault_code, :suggested_category) "
        "ON CONFLICT (start_ts) DO NOTHING"
    ),
    CloseStopOp: text(
        "UPDATE stops SET end_ts = :end_ts, "
        "duration_s = EXTRACT(EPOCH FROM (CAST(:end_ts AS timestamptz) - start_ts)) "
        "WHERE start_ts = :start_ts AND end_ts IS NULL AND start_ts < :end_ts"
    ),
    CounterSampleOp: text(
        "INSERT INTO counter_samples (ts, good_total, scrap_total, theoretical_cycle_s, "
        "last_cycle_s) VALUES (:ts, :good, :scrap, :theoretical_cycle_s, :last_cycle_s) "
        "ON CONFLICT (ts) DO NOTHING"
    ),
    WeldSampleOp: text(
        "INSERT INTO weld_samples (ts, current_ka, points_since_cap_change, ok) "
        "VALUES (:ts, :current_ka, :points_since_cap_change, :ok) ON CONFLICT (ts) DO NOTHING"
    ),
    OpenGapOp: text(
        "INSERT INTO comm_gaps (start_ts) VALUES (:start_ts) ON CONFLICT (start_ts) DO NOTHING"
    ),
    CloseGapOp: text(
        "UPDATE comm_gaps SET end_ts = :end_ts "
        "WHERE start_ts = :start_ts AND end_ts IS NULL AND start_ts < :end_ts"
    ),
    ClosedGapOp: text(
        "INSERT INTO comm_gaps (start_ts, end_ts) VALUES (:start_ts, :end_ts) "
        "ON CONFLICT (start_ts) DO NOTHING"
    ),
}

_LAST_SEEN = text(
    "INSERT INTO collector_state (id, last_seen_ts) VALUES (1, :ts) "
    "ON CONFLICT (id) DO UPDATE SET "
    "last_seen_ts = GREATEST(collector_state.last_seen_ts, EXCLUDED.last_seen_ts), "
    "updated_at = now()"
)


def _params(op: Op) -> dict[str, Any]:
    return {name: getattr(op, name) for name in op.__slots__}  # dataclass(slots=True)


@dataclass(slots=True)
class Resume:
    """Everything the collector needs to pick up where the database left off."""

    tracker: TrackerState
    last_seen_ts: datetime | None
    open_gap_start: datetime | None


class CollectorStore:
    def __init__(self, engine: AsyncEngine, notify_channel: str | None = None) -> None:
        self._engine = engine
        self._notify_channel = notify_channel

    async def apply(self, ops: Sequence[Op], last_seen: datetime | None = None) -> None:
        """Write a batch in one transaction, in order; runs of the same operation are batched."""
        if not ops and last_seen is None:
            return
        async with self._engine.begin() as conn:
            run: list[dict[str, Any]] = []
            run_type: type | None = None
            for op in ops:
                if type(op) is not run_type:
                    await self._flush(conn, run_type, run)
                    run, run_type = [], type(op)
                run.append(_params(op))
            await self._flush(conn, run_type, run)
            if last_seen is not None:
                await conn.execute(_LAST_SEEN, {"ts": last_seen})
            if self._notify_channel:
                # A wake-up hint, not a data channel: the API always re-reads the database. The
                # payload is delivered only after this transaction commits (Postgres semantics).
                payload = json.dumps({"now": last_seen.isoformat() if last_seen else None})
                await conn.execute(
                    text("SELECT pg_notify(:channel, :payload)"),
                    {"channel": self._notify_channel, "payload": payload},
                )

    @staticmethod
    async def _flush(
        conn: AsyncConnection, op_type: type | None, rows: list[dict[str, Any]]
    ) -> None:
        if op_type is not None and rows:
            await conn.execute(_SQL[op_type], rows)

    async def resume(self) -> Resume:
        """Repair the open stop if needed, then read the state to resume from."""
        async with self._engine.begin() as conn:
            await self._repair_open_stop(conn)
            state = TrackerState()
            row = (
                await conn.execute(
                    text(
                        "SELECT ts, state, fault_code, stop_origin FROM state_events "
                        "WHERE scope = 'CELL' ORDER BY ts DESC LIMIT 1"
                    )
                )
            ).first()
            if row:
                state.last_cell = (row.ts, row.state, row.fault_code, row.stop_origin)
            for scope in ("OP10", "OP20", "OP30"):
                srow = (
                    await conn.execute(
                        text(
                            "SELECT ts, state, fault_code FROM state_events "
                            "WHERE scope = :scope ORDER BY ts DESC LIMIT 1"
                        ),
                        {"scope": scope},
                    )
                ).first()
                if srow:
                    state.last_station[scope] = (srow.ts, srow.state, srow.fault_code)
            state.open_stop_start = (
                await conn.execute(text("SELECT start_ts FROM stops WHERE end_ts IS NULL"))
            ).scalar_one_or_none()
            state.last_counter_ts = (
                await conn.execute(text("SELECT max(ts) FROM counter_samples"))
            ).scalar_one()
            state.last_weld_ts = (
                await conn.execute(text("SELECT max(ts) FROM weld_samples"))
            ).scalar_one()
            last_seen = (
                await conn.execute(text("SELECT last_seen_ts FROM collector_state"))
            ).scalar_one_or_none()
            open_gap = (
                await conn.execute(text("SELECT start_ts FROM comm_gaps WHERE end_ts IS NULL"))
            ).scalar_one_or_none()
        return Resume(state, last_seen, open_gap)

    async def _repair_open_stop(self, conn: AsyncConnection) -> None:
        """Make the open stop agree with the latest cell state event.

        Writes are transactional, so this is a safety net (manual edits, an older version of the
        collector): the stops table must never disagree with the events it is derived from.
        """
        event = (
            await conn.execute(
                text(
                    "SELECT ts, state, fault_code, stop_origin FROM state_events "
                    "WHERE scope = 'CELL' ORDER BY ts DESC LIMIT 1"
                )
            )
        ).first()
        if event is None:
            return
        open_start = (
            await conn.execute(text("SELECT start_ts FROM stops WHERE end_ts IS NULL"))
        ).scalar_one_or_none()
        producing = event.state == CellState.PRODUCING
        if open_start is not None and (producing or open_start != event.ts):
            log.warning("repairing open stop %s: latest cell event is at %s", open_start, event.ts)
            await conn.execute(
                _SQL[CloseStopOp],
                {"start_ts": open_start, "end_ts": max(event.ts, open_start)},
            )
            open_start = None if event.ts > open_start else open_start
        if not producing and open_start is None:
            already = (
                await conn.execute(
                    text("SELECT 1 FROM stops WHERE start_ts = :ts"), {"ts": event.ts}
                )
            ).first()
            if already is None:
                log.warning("repairing: reopening the stop that starts at %s", event.ts)
                cause = suggest_cause(event.state, event.fault_code)
                await conn.execute(
                    _SQL[OpenStopOp],
                    {
                        "start_ts": event.ts,
                        "state": event.state,
                        "origin_station": STATION_OF_ORIGIN.get(event.stop_origin or 0, "CELL"),
                        "fault_code": event.fault_code,
                        "suggested_category": cause.value if cause else None,
                    },
                )
