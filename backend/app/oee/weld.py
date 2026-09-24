"""Weld current and scrap rate, bucketed in time: the electrode-wear curve.

Pure function. Bucket boundaries are computed the same way SQL's ``time_bucket(bucket, ts,
origin)`` would (a test checks the two agree), aligned on the production day start so buckets
line up with shifts. The scrap rate is *not* SQL: it is read off the same counter deltas the OEE
core uses, so a communication gap drops a part here exactly as it does in ``compute_oee`` — a
bucket cannot show a suspicious rebot spike just because production was unobserved for a while.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from .types import CounterDelta, Gap


@dataclass(frozen=True, slots=True)
class WeldPoint:
    ts: datetime
    current_ka: float
    ok: bool


@dataclass(frozen=True, slots=True)
class WeldBucket:
    start: datetime
    end: datetime
    avg_ka: float | None
    min_ka: float | None
    max_ka: float | None
    points: int
    points_nok: int
    good: int
    scrap: int

    @property
    def scrap_rate(self) -> float | None:
        total = self.good + self.scrap
        return None if total == 0 else self.scrap / total


def bucket_start(ts: datetime, origin: datetime, bucket_s: float) -> datetime:
    """The start of the bucket ``ts`` falls into, aligned on ``origin`` (same rule as
    PostgreSQL's ``time_bucket(bucket, ts, origin)``)."""
    offset = (ts - origin).total_seconds()
    index = math.floor(offset / bucket_s)
    return origin + timedelta(seconds=index * bucket_s)


def choose_bucket_s(window_s: float, target_buckets: int = 300, floor_s: float = 60.0) -> float:
    """A bucket width that keeps the number of buckets reasonable for any requested range."""
    return max(floor_s, math.ceil(window_s / target_buckets / floor_s) * floor_s)


def _overlaps_gap(delta: CounterDelta, gaps: Sequence[Gap]) -> bool:
    if delta.prev_ts is None:
        return False
    return any(g.start < delta.ts and (g.end is None or g.end > delta.prev_ts) for g in gaps)


def compute_weld_buckets(
    points: Sequence[WeldPoint],
    deltas: Sequence[CounterDelta],
    gaps: Sequence[Gap],
    frm: datetime,
    to: datetime,
    bucket_s: float,
    day_origin: datetime,
) -> list[WeldBucket]:
    """One bucket per ``bucket_s`` interval covering [frm, to), aligned on ``day_origin``.

    A bucket with no weld point has null current stats (a broken line on the chart, not a drop
    to zero); one with no delta has a null scrap rate.
    """
    first = bucket_start(frm, day_origin, bucket_s)
    starts: list[datetime] = []
    cursor = first
    while cursor < to:
        starts.append(cursor)
        cursor += timedelta(seconds=bucket_s)

    idx = {s: i for i, s in enumerate(starts)}
    currents: list[list[float]] = [[] for _ in starts]
    nok: list[int] = [0] * len(starts)
    good: list[int] = [0] * len(starts)
    scrap: list[int] = [0] * len(starts)

    for p in points:
        if not frm <= p.ts < to:
            continue
        i = idx.get(bucket_start(p.ts, day_origin, bucket_s))
        if i is None:
            continue
        currents[i].append(p.current_ka)
        if not p.ok:
            nok[i] += 1

    for d in deltas:
        if not frm <= d.ts < to or _overlaps_gap(d, gaps):
            continue
        i = idx.get(bucket_start(d.ts, day_origin, bucket_s))
        if i is None:
            continue
        good[i] += d.good
        scrap[i] += d.scrap

    out: list[WeldBucket] = []
    for i, start in enumerate(starts):
        vals = currents[i]
        out.append(
            WeldBucket(
                start=start,
                end=start + timedelta(seconds=bucket_s),
                avg_ka=sum(vals) / len(vals) if vals else None,
                min_ka=min(vals) if vals else None,
                max_ka=max(vals) if vals else None,
                points=len(vals),
                points_nok=nok[i],
                good=good[i],
                scrap=scrap[i],
            )
        )
    return out
