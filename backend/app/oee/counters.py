"""Cumulative UInt32 counters -> production deltas, without ever going negative."""

from __future__ import annotations

from collections.abc import Sequence

from .types import CounterDelta, CounterSample, DeltaKind

UINT32_RANGE = 2**32


def _delta(prev: int, cur: int, rollover_window: int) -> tuple[int, DeltaKind]:
    if cur >= prev:
        return cur - prev, DeltaKind.NORMAL
    if prev >= UINT32_RANGE - rollover_window and cur <= rollover_window:
        return cur + UINT32_RANGE - prev, DeltaKind.ROLLOVER
    # A decrease that is not a wrap-around is a PLC restart: whatever was produced since is
    # unknown, so it is counted as nothing rather than guessed. The new value is the baseline.
    return 0, DeltaKind.RESET


_SEVERITY = {DeltaKind.NORMAL: 0, DeltaKind.ROLLOVER: 1, DeltaKind.RESET: 2}


def counter_deltas(
    samples: Sequence[CounterSample], rollover_window: int = 1000
) -> list[CounterDelta]:
    """One delta per sample, in timestamp order (the first has no baseline: zero parts).

    Each delta belongs to the instant of the sample that revealed it, not to the interval before.
    """
    ordered = sorted(samples, key=lambda s: s.ts)
    out: list[CounterDelta] = []
    prev: CounterSample | None = None
    for s in ordered:
        if prev is None:
            out.append(CounterDelta(s.ts, None, 0, 0, s.theoretical_cycle_s, DeltaKind.FIRST))
        else:
            good, good_kind = _delta(prev.good_total, s.good_total, rollover_window)
            scrap, scrap_kind = _delta(prev.scrap_total, s.scrap_total, rollover_window)
            kind = max(good_kind, scrap_kind, key=_SEVERITY.__getitem__)
            out.append(CounterDelta(s.ts, prev.ts, good, scrap, s.theoretical_cycle_s, kind))
        prev = s
    return out
