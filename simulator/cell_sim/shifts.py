"""Shift calendar and planned breaks, in simulated plant time (no DST)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from .config import PlannedBreakCfg, ShiftCfg

DAY_S = 86_400


@dataclass(frozen=True, slots=True)
class BreakWindow:
    start: float
    end: float


class ShiftCalendar:
    def __init__(self, shifts: tuple[ShiftCfg, ...], planned_break: PlannedBreakCfg) -> None:
        self._shifts = shifts
        self._offset_s = planned_break.offset_h * 3600
        self._duration_s = planned_break.duration_min * 60
        self._starts = sorted(s.start_s for s in shifts)

    def shift_at(self, ts: float) -> ShiftCfg:
        tod = ts % DAY_S
        for s in self._shifts:
            if s.start_s <= s.end_s:
                if s.start_s <= tod < s.end_s:
                    return s
            elif tod >= s.start_s or tod < s.end_s:  # crosses midnight
                return s
        raise AssertionError("shifts do not cover 24 h")  # guarded by config validation

    def _windows(self, first_day: int) -> Iterator[BreakWindow]:
        day = first_day
        while True:
            for s in self._starts:
                start = day * DAY_S + s + self._offset_s
                yield BreakWindow(start, start + self._duration_s)
            day += 1

    def next_window(self, after: float) -> BreakWindow:
        """First break window that has not ended yet at ``after`` (it may be in progress)."""
        for w in self._windows(int(after // DAY_S) - 2):
            if w.end > after:
                return w
        raise AssertionError("unreachable")  # infinite generator
