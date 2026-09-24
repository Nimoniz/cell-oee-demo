"""Cell state timeline: the sequence of states as [start, end) segments.

Pure, like the rest of ``app.oee``. Used for the live screen's andon history bar — a different
view of the same ``state_events`` the collector already writes, not a new source of truth.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from .types import Window


@dataclass(frozen=True, slots=True)
class StateEvent:
    ts: datetime
    state: int


@dataclass(frozen=True, slots=True)
class TimelineSegment:
    start: datetime
    end: datetime
    state: int


def build_timeline(
    events: Sequence[StateEvent], window: Window, until: datetime | None = None
) -> list[TimelineSegment]:
    """One segment per state change, clipped to ``window``.

    ``events`` should include the last event before ``window.start`` if one exists (the "lead-in",
    same idea as the counter deltas' baseline sample) so the very first segment has a state
    instead of starting on a gap.
    """
    bound = until if until is not None else window.end
    ordered = sorted((e for e in events if e.ts < bound), key=lambda e: e.ts)
    segments: list[TimelineSegment] = []
    for i, e in enumerate(ordered):
        end = min(ordered[i + 1].ts if i + 1 < len(ordered) else bound, bound)
        start = max(e.ts, window.start)
        if end > start:
            segments.append(TimelineSegment(start, end, e.state))
    return segments
