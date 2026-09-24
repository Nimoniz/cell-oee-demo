"""Turns groups of changes into database operations, idempotently.

The tracker is a pure state machine. Its starting point is what is *already stored* (see
``TrackerState``), never what the PLC currently says: when a subscription is created, the server
sends the current value of every tag with the SourceTimestamp of its last change. Those replays
match what the database already holds and produce nothing; a real change produces exactly one
event, whichever notification carries it, however many times it is delivered.

Rules:
* a cell event is built from the latest known State, FaultCode and StopOrigin, not only from the
  tags that changed at that instant (FaultCode is often unchanged when State changes);
* an event whose values equal the last stored event is a replay: ignored;
* an event older than the last stored one is never inserted (history is append-only); one at the
  same instant with different values is a conflict: logged and ignored;
* every State change closes the open stop and, if the new state is not PRODUCING, opens a new one
  at the source timestamp of the change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from app.domain import STATION_OF_ORIGIN, CellState, suggest_cause

from .types import (
    CELL_FAULT,
    CELL_ORIGIN,
    CELL_STATE,
    GOOD,
    HEARTBEAT,
    LAST_CYCLE,
    SCRAP,
    STATIONS,
    TCT,
    WELD_CURRENT,
    WELD_OK,
    WELD_POINTS,
    CloseStopOp,
    CounterSampleOp,
    Group,
    Op,
    OpenStopOp,
    StateEventOp,
    TagValue,
    WeldSampleOp,
)

log = logging.getLogger(__name__)

_CELL_TAGS = {CELL_STATE, CELL_FAULT, CELL_ORIGIN}
_COUNTER_TRIGGERS = {GOOD, SCRAP}


@dataclass(slots=True)
class TrackerState:
    """What the database already holds; the tracker's only memory across restarts."""

    last_cell: tuple[datetime, int, int, int] | None = None  # ts, state, fault, origin
    last_station: dict[str, tuple[datetime, int, int]] = field(default_factory=dict)
    open_stop_start: datetime | None = None
    last_counter_ts: datetime | None = None
    last_weld_ts: datetime | None = None


class StopTracker:
    def __init__(self, state: TrackerState | None = None) -> None:
        self.state = state or TrackerState()
        self._latest: dict[str, tuple[datetime, TagValue]] = {}

    def apply(self, group: Group) -> list[Op]:
        changed = set(group.values) - {HEARTBEAT}
        for tag in changed:
            known = self._latest.get(tag)
            if known is None or group.ts >= known[0]:
                self._latest[tag] = (group.ts, group.values[tag])
        ops: list[Op] = []
        ops += self._cell_event(group.ts, changed)
        ops += self._station_events(group.ts, changed)
        ops += self._counter_sample(group.ts, changed)
        ops += self._weld_sample(group.ts, changed)
        return ops

    def _value(self, tag: str) -> TagValue | None:
        known = self._latest.get(tag)
        return None if known is None else known[1]

    # ------------------------------------------------------------------ cell state and stops

    def _cell_event(self, ts: datetime, changed: set[str]) -> list[Op]:
        if not changed & _CELL_TAGS:
            return []
        state, fault, origin = (
            self._value(CELL_STATE),
            self._value(CELL_FAULT),
            self._value(CELL_ORIGIN),
        )
        if state is None or fault is None or origin is None:
            return []  # the first values are still arriving
        values = (int(state), int(fault), int(origin))
        last = self.state.last_cell
        if last is not None:
            last_ts, *last_values = last
            if tuple(last_values) == values:
                return []  # a replay, or nothing actually changed
            if ts <= last_ts:
                log.warning(
                    "ignoring cell event at %s: %s conflicts with %s stored at %s",
                    ts,
                    values,
                    tuple(last_values),
                    last_ts,
                )
                return []
        return self._transition(ts, *values)

    def _transition(self, ts: datetime, state: int, fault: int, origin: int) -> list[Op]:
        ops: list[Op] = [StateEventOp("CELL", ts, state, fault, origin)]
        if self.state.open_stop_start is not None:
            ops.append(CloseStopOp(self.state.open_stop_start, ts))
            self.state.open_stop_start = None
        if state != CellState.PRODUCING:
            station = STATION_OF_ORIGIN.get(origin)
            if station is None:
                log.warning("stop at %s has no usable origin (%s): assigned to CELL", ts, origin)
                station = "CELL"
            cause = suggest_cause(state, fault)
            ops.append(OpenStopOp(ts, state, station, fault, cause.value if cause else None))
            self.state.open_stop_start = ts
        self.state.last_cell = (ts, state, fault, origin)
        return ops

    # ------------------------------------------------------------------ stations

    def _station_events(self, ts: datetime, changed: set[str]) -> list[Op]:
        ops: list[Op] = []
        for scope in STATIONS:
            if not changed & {f"{scope}/State", f"{scope}/FaultCode"}:
                continue
            state, fault = self._value(f"{scope}/State"), self._value(f"{scope}/FaultCode")
            if state is None or fault is None:
                continue
            values = (int(state), int(fault))
            last = self.state.last_station.get(scope)
            if last is not None and (last[1:] == values or ts <= last[0]):
                continue
            self.state.last_station[scope] = (ts, *values)
            ops.append(StateEventOp(scope, ts, values[0], values[1], None))
        return ops

    # ------------------------------------------------------------------ samples

    def _counter_sample(self, ts: datetime, changed: set[str]) -> list[Op]:
        if not changed & _COUNTER_TRIGGERS:
            return []
        good, scrap = self._value(GOOD), self._value(SCRAP)
        tct, cycle = self._value(TCT), self._value(LAST_CYCLE)
        if good is None or scrap is None or tct is None or cycle is None:
            return []
        last = self.state.last_counter_ts
        if last is not None and ts <= last:
            return []  # already stored (replay after a restart) or out of order
        self.state.last_counter_ts = ts
        return [CounterSampleOp(ts, int(good), int(scrap), float(tct), float(cycle))]

    def _weld_sample(self, ts: datetime, changed: set[str]) -> list[Op]:
        if WELD_CURRENT not in changed:  # one sample per weld point, not per cap change
            return []
        current, points, ok = (
            self._value(WELD_CURRENT),
            self._value(WELD_POINTS),
            self._value(WELD_OK),
        )
        if current is None or points is None or ok is None:
            return []
        last = self.state.last_weld_ts
        if last is not None and ts <= last:
            return []
        self.state.last_weld_ts = ts
        return [WeldSampleOp(ts, float(current), int(points), bool(ok))]
