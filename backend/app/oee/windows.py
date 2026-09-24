"""Reporting windows: shifts and production days, clipped to the requested range."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from .types import ShiftPlan, Window

GroupBy = Literal["shift", "day"]

_DAY = timedelta(days=1)


def _midnight(ts: datetime) -> datetime:
    return ts.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _clip(label: str, start: datetime, end: datetime, lo: datetime, hi: datetime) -> Window | None:
    start, end = max(start, lo), min(end, hi)
    return Window(label, start, end) if end > start else None


def shift_windows(frm: datetime, to: datetime, plan: ShiftPlan) -> list[Window]:
    """Every shift overlapping [frm, to), clipped to it, labelled ``"<date> <name>"``.

    A shift belongs to the date it starts on: the night shift of 2026-01-05 runs until 05:00
    on the 6th.
    """
    out: list[Window] = []
    day = _midnight(frm) - _DAY
    while day < to:
        for shift in plan.shifts:
            start = day + timedelta(seconds=shift.start_s)
            length = (shift.end_s - shift.start_s) % 86_400 or 86_400
            window = _clip(
                f"{start.date()} {shift.name}", start, start + timedelta(seconds=length), frm, to
            )
            if window:
                out.append(window)
        day += _DAY
    return sorted(out, key=lambda w: w.start)


def day_windows(frm: datetime, to: datetime, plan: ShiftPlan) -> list[Window]:
    """Production days (``day_start`` -> ``day_start`` + 24 h), clipped to [frm, to)."""
    out: list[Window] = []
    day = _midnight(frm) - _DAY
    while day < to:
        start = day + timedelta(seconds=plan.day_start_s)
        window = _clip(str(start.date()), start, start + _DAY, frm, to)
        if window:
            out.append(window)
        day += _DAY
    return out


def make_windows(frm: datetime, to: datetime, group_by: GroupBy, plan: ShiftPlan) -> list[Window]:
    if group_by == "shift":
        return shift_windows(frm, to, plan)
    return day_windows(frm, to, plan)
