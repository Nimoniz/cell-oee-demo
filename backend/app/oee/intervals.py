"""Interval arithmetic on (lo, hi) float pairs, used for exact time accounting."""

from __future__ import annotations

from collections.abc import Iterable

Interval = tuple[float, float]


def merge(intervals: Iterable[Interval]) -> list[Interval]:
    """Sorted, disjoint, non-empty union."""
    out: list[Interval] = []
    for lo, hi in sorted(iv for iv in intervals if iv[1] > iv[0]):
        if out and lo <= out[-1][1]:
            if hi > out[-1][1]:
                out[-1] = (out[-1][0], hi)
        else:
            out.append((lo, hi))
    return out


def clip(intervals: Iterable[Interval], lo: float, hi: float) -> list[Interval]:
    return merge((max(a, lo), min(b, hi)) for a, b in intervals)


def total(intervals: Iterable[Interval]) -> float:
    return sum(hi - lo for lo, hi in intervals)


def complement(intervals: list[Interval], lo: float, hi: float) -> list[Interval]:
    """What is left of [lo, hi] once ``intervals`` (merged) are removed."""
    out: list[Interval] = []
    cursor = lo
    for a, b in intervals:
        if a > cursor:
            out.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < hi:
        out.append((cursor, hi))
    return out


def intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    """Intersection of two merged lists."""
    out: list[Interval] = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi > lo:
            out.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def subtract(a: list[Interval], holes: list[Interval]) -> list[Interval]:
    """``a`` (merged) minus ``holes`` (merged)."""
    out: list[Interval] = []
    for lo, hi in a:
        cursor = lo
        for h_lo, h_hi in holes:
            if h_hi <= cursor or h_lo >= hi:
                continue
            if h_lo > cursor:
                out.append((cursor, h_lo))
            cursor = max(cursor, h_hi)
        if cursor < hi:
            out.append((cursor, hi))
    return out
