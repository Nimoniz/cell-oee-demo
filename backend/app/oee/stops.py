"""Stop classification: planned, availability loss, or micro-stop."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from .types import ClassifiedStop, StopKind, StopRecord

PLANNED_STOP = 2
PRODUCING = 1


def classify_stops(
    stops: Sequence[StopRecord], until: datetime, micro_stop_s: float = 120.0
) -> list[ClassifiedStop]:
    """Classify stops on their *whole* duration, never on the part inside a window.

    A stop of 130 s split 65 s / 65 s by a shift boundary is still a stop in both shifts, and a
    100 s stop is a micro-stop wherever it falls. Open stops last until ``until`` (the latest
    simulated time), so one that crosses the threshold becomes a loss retroactively, from its start.
    """
    out: list[ClassifiedStop] = []
    for s in stops:
        if s.state == PRODUCING:
            raise ValueError("a stop cannot be in the PRODUCING state")
        end = s.end if s.end is not None else max(until, s.start)
        duration = (end - s.start).total_seconds()
        if s.state == PLANNED_STOP:
            kind = StopKind.PLANNED
        elif duration < micro_stop_s:
            kind = StopKind.MICRO
        else:
            kind = StopKind.LOSS
        out.append(ClassifiedStop(s.start, end, s.state, kind))
    return out
