"""Stop Pareto: availability losses grouped by cause, station or fault code.

Pure function, like the rest of ``app.oee``: it takes stop facts (a superset of ``StopRecord``
that also carries origin, fault code and qualification) and a window, and returns aggregated
buckets. Planned stops are excluded (nothing to analyse); micro-stops are excluded from the
buckets but summarised separately, exactly as they are excluded from availability losses in
``compute_oee`` — the two must add up to the same total, which is the mandatory invariant test.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from . import intervals as iv
from .types import Gap, Window

PLANNED_STOP = 2
By = Literal["cause", "station", "fault_code"]
Metric = Literal["duration", "count"]

UNQUALIFIED = "unqualified"


@dataclass(frozen=True, slots=True)
class StopFact:
    """A stop as stored, with everything the Pareto needs (a superset of ``StopRecord``)."""

    start: datetime
    end: datetime | None  # None while open
    state: int
    origin_station: str
    fault_code: int
    qualified_cause: str | None


@dataclass(frozen=True, slots=True)
class ParetoBucket:
    key: str  # a Cause value, a station name, a fault code as text, or "state_<n>"
    duration_s: float
    count: int
    unqualified_duration_s: float
    unqualified_count: int


@dataclass(frozen=True, slots=True)
class ParetoResult:
    by: By
    metric: Metric
    buckets: tuple[ParetoBucket, ...]  # sorted by the chosen metric, descending
    micro_count: int
    micro_duration_s: float


def _key(fact: StopFact, by: By) -> str:
    if by == "cause":
        return fact.qualified_cause or UNQUALIFIED
    if by == "station":
        return fact.origin_station
    if fact.fault_code:
        return str(fact.fault_code)
    return f"state_{fact.state}"  # SETUP / STARVED / BLOCKED / MANUAL carry no fault code


def compute_pareto(
    facts: Sequence[StopFact],
    window: Window,
    gaps: Sequence[Gap],
    by: By,
    metric: Metric,
    micro_stop_s: float = 120.0,
    until: datetime | None = None,
) -> ParetoResult:
    length = window.length_s
    bound = until if until is not None else window.end

    def rel(ts: datetime) -> float:
        return (ts - window.start).total_seconds()

    gap_iv = iv.clip(((rel(g.start), rel(g.end) if g.end else length) for g in gaps), 0.0, length)
    observed = iv.complement(gap_iv, 0.0, length)

    totals: dict[str, list[float]] = {}  # key -> [duration_s, count, unq_duration_s, unq_count]
    micro_count = 0
    micro_duration_s = 0.0

    for fact in facts:
        if fact.state == PLANNED_STOP:
            continue
        end = fact.end if fact.end is not None else max(bound, fact.start)
        if end <= window.start or fact.start >= window.end:
            continue  # no overlap with the window at all
        duration = (end - fact.start).total_seconds()
        overlap = iv.total(
            iv.intersect(iv.clip([(rel(fact.start), rel(end))], 0.0, length), observed)
        )
        starts_here = window.start <= fact.start < window.end

        if duration < micro_stop_s:
            if starts_here:
                micro_count += 1
                micro_duration_s += overlap
            continue

        row = totals.setdefault(_key(fact, by), [0.0, 0, 0.0, 0])
        row[0] += overlap
        if starts_here:
            row[1] += 1
        if fact.qualified_cause is None:
            row[2] += overlap
            if starts_here:
                row[3] += 1

    buckets = [ParetoBucket(key, d, int(c), ud, int(uc)) for key, (d, c, ud, uc) in totals.items()]
    value = (lambda b: b.duration_s) if metric == "duration" else (lambda b: b.count)
    # The "unqualified" bucket (by=cause) always trails, as a dedicated bar.
    buckets.sort(key=lambda b: (b.key == UNQUALIFIED, -value(b)))
    return ParetoResult(by, metric, tuple(buckets), micro_count, micro_duration_s)
