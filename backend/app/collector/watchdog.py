"""Heartbeat watchdog: detects communication loss and records gaps.

Communication is lost when the heartbeat has not changed for ``timeout_s`` of *wall-clock* time
(``Settings.heartbeat_timeout_s`` = max(3 s, 5 s simulated / ACCELERATION)). Wall-clock time is
what makes this work for a real loss: when the PLC or the network dies, simulated time stops.

A gap starts at the SourceTimestamp of the last heartbeat and ends at the SourceTimestamp of the
first heartbeat received afterwards: both are simulated time, so the gap is directly comparable
with stops and counter samples. A heartbeat is "new" when its timestamp advances, whatever its
value (a PLC restart resets the value to 0).

The collector itself can also be the blind party: after a (re)connection, the first heartbeat is
compared with the last one seen, before the restart or the disconnection. If simulated time
jumped by more than ``jump_s``, the interval is recorded as a gap too (state changes may have been
missed in it).
"""

from __future__ import annotations

from datetime import datetime

from .types import ClosedGapOp, CloseGapOp, Op, OpenGapOp


class HeartbeatWatchdog:
    def __init__(
        self,
        timeout_s: float,
        now: float,
        jump_s: float = 5.0,
        last_seen_ts: datetime | None = None,
        open_gap_start: datetime | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._jump_s = jump_s
        self._last_hb: datetime | None = last_seen_ts
        self._last_change_wall = now
        self._gap_start = open_gap_start
        self._check_jump = last_seen_ts is not None

    @property
    def in_gap(self) -> bool:
        return self._gap_start is not None

    def seed(self, first_ts: datetime) -> None:
        """First ever run (empty database): observation starts now, not when the PLC did.

        The current values sent on the first subscription carry old timestamps. Time between the
        oldest of them and the first heartbeat was never observed: it becomes a gap, so a
        collector that starts a few seconds after the PLC does not report a perfect first minute.
        """
        if self._last_hb is None:
            self._last_hb = first_ts
            self._check_jump = True

    def on_connected(self, now: float) -> None:
        """A (re)connection: give the new session a full timeout, and check for a time jump."""
        self._last_change_wall = now
        self._check_jump = self._last_hb is not None

    def on_heartbeat(self, ts: datetime, now: float) -> list[Op]:
        if self._last_hb is not None and ts <= self._last_hb:
            return []  # a replay (e.g. the current value sent when a subscription is created)
        ops: list[Op] = []
        if self._gap_start is not None:
            ops.append(CloseGapOp(self._gap_start, ts))
            self._gap_start = None
        elif (
            self._check_jump
            and self._last_hb is not None
            and (ts - self._last_hb).total_seconds() > self._jump_s
        ):
            ops.append(ClosedGapOp(self._last_hb, ts))
        self._check_jump = False
        self._last_hb = ts
        self._last_change_wall = now
        return ops

    def poll(self, now: float) -> list[Op]:
        if (
            self._gap_start is None
            and self._last_hb is not None
            and now - self._last_change_wall > self._timeout_s
        ):
            self._gap_start = self._last_hb
            return [OpenGapOp(self._gap_start)]
        return []
