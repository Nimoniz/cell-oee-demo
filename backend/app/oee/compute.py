"""OEE (TRS) computed from stops, counter samples and communication gaps, in time (NF E60-182).

Nothing is pre-aggregated: every function works on raw facts, so any window (shift, day, an
arbitrary range) can be evaluated after the fact, with the micro-stop threshold of the day.

    required time  (``planned_s``)  = opening time - planned stops   (opening excludes comm gaps)
    operating time (``operating_s``) = required - (fault + setup + starved + blocked + manual)
                                       [micro-stops stay inside]
    net time       (``total_tct_s``) = total parts x theoretical cycle time
    useful time    (``good_tct_s``)  = good parts x theoretical cycle time
    A = operating / required    P = net / operating    Q = useful / net
    OEE = A x P x Q = useful / required                <- reference invariant
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import datetime

from . import intervals as iv
from .counters import counter_deltas
from .stops import classify_stops
from .types import (
    DEFAULT_PARAMS,
    ClassifiedStop,
    CounterDelta,
    CounterSample,
    Gap,
    OeeParams,
    OeeResult,
    StopKind,
    StopRecord,
    Window,
)
from .windows import GroupBy, ShiftPlan, make_windows

# Loss categories, in the order they claim time if stops ever overlap (they should not).
_LOSS_STATES = ((3, "fault"), (4, "setup"), (5, "starved"), (6, "blocked"), (7, "manual"))


class OeeInvariantError(AssertionError):
    """The OEE accounting is inconsistent: this is a bug, never a data problem."""


def _ratios(
    planned_s: float, operating_s: float, good_tct_s: float, total_tct_s: float
) -> tuple[float | None, float | None, float | None, float | None]:
    """Availability, performance, quality, OEE from times and cycle-time-weighted parts.

    Quality is useful time / net time (NF E60-182): each part is weighted by the theoretical
    cycle time in force when it was made. With a constant cycle time this is exactly
    good / total parts, and it keeps OEE == A x P x Q even if the cycle time changes in the period.
    """
    if planned_s <= 0:
        return None, None, None, None  # nothing was required: no OEE, which is not the same as 0
    availability = operating_s / planned_s
    performance = total_tct_s / operating_s if operating_s > 0 else None
    quality = good_tct_s / total_tct_s if total_tct_s > 0 else None
    if performance is not None and quality is not None:
        oee = availability * performance * quality
    else:
        oee = good_tct_s / planned_s  # same quantity, defined even when P or Q is not
    return availability, performance, quality, oee


def _anomalies(operating_s: float, total_parts: int) -> tuple[str, ...]:
    if total_parts > 0 and operating_s <= 0:
        return ("parts_without_operating_time",)
    return ()


def _overlaps_gap(delta: CounterDelta, gaps: Sequence[Gap]) -> bool:
    """True if the interval since the previous sample touches a gap: production was not observed."""
    if delta.prev_ts is None:
        return False
    return any(g.start < delta.ts and (g.end is None or g.end > delta.prev_ts) for g in gaps)


def compute_oee(
    window: Window,
    stops: Sequence[ClassifiedStop],
    deltas: Sequence[CounterDelta],
    gaps: Sequence[Gap],
) -> OeeResult:
    """OEE of one window.

    ``stops`` must be classified on their full duration (see ``classify_stops``); a stop that
    straddles the window contributes exactly its overlap, i.e. it is split pro rata to time.
    """
    length = window.length_s

    def rel(ts: datetime) -> float:  # relative offsets keep float precision on 1.7e9 timestamps
        return (ts - window.start).total_seconds()

    gap_iv = iv.clip(((rel(g.start), rel(g.end) if g.end else length) for g in gaps), 0.0, length)
    observed = iv.complement(gap_iv, 0.0, length)

    def within(kinds: Iterable[StopKind], states: Iterable[int] | None = None) -> list[iv.Interval]:
        wanted = None if states is None else set(states)
        raw = [
            (rel(s.start), rel(s.end))
            for s in stops
            if s.kind in kinds and (wanted is None or s.state in wanted)
        ]
        return iv.intersect(iv.clip(raw, 0.0, length), observed)

    # Each second is claimed once: planned stop > fault > setup > starved > blocked > manual.
    taken = within({StopKind.PLANNED})
    planned_stop_s = iv.total(taken)
    planned_s = iv.total(iv.subtract(observed, taken))
    losses: dict[str, float] = {}
    for state, name in _LOSS_STATES:
        mine = iv.subtract(within({StopKind.LOSS}, {state}), taken)
        losses[name] = iv.total(mine)
        taken = iv.merge(taken + mine)
    micro_s = iv.total(iv.subtract(within({StopKind.MICRO}), taken))
    operating_s = iv.total(iv.subtract(observed, taken))

    good = scrap = dropped = 0
    good_tct_s = total_tct_s = 0.0
    for d in deltas:
        if not window.start <= d.ts < window.end:
            continue
        if _overlaps_gap(d, gaps):
            dropped += d.parts
            continue
        good += d.good
        scrap += d.scrap
        good_tct_s += d.good * d.theoretical_cycle_s
        total_tct_s += d.parts * d.theoretical_cycle_s

    availability, performance, quality, oee = _ratios(
        planned_s, operating_s, good_tct_s, total_tct_s
    )
    return OeeResult(
        window=window,
        gap_s=iv.total(gap_iv),
        opening_s=iv.total(observed),
        planned_stop_s=planned_stop_s,
        planned_s=planned_s,
        fault_s=losses["fault"],
        setup_s=losses["setup"],
        starved_s=losses["starved"],
        blocked_s=losses["blocked"],
        manual_s=losses["manual"],
        micro_s=micro_s,
        operating_s=operating_s,
        good=good,
        scrap=scrap,
        good_tct_s=good_tct_s,
        total_tct_s=total_tct_s,
        dropped_parts=dropped,
        availability=availability,
        performance=performance,
        quality=quality,
        oee=oee,
        anomalies=_anomalies(operating_s, good + scrap),
    )


def compute_oee_windows(
    windows: Sequence[Window],
    stops: Sequence[StopRecord],
    samples: Sequence[CounterSample],
    gaps: Sequence[Gap],
    params: OeeParams = DEFAULT_PARAMS,
    until: datetime | None = None,
) -> list[OeeResult]:
    """OEE of each window from the raw facts.

    ``until`` is the latest simulated time received: it bounds open stops. It defaults to the
    end of the last window.
    """
    if not windows:
        return []
    bound = until if until is not None else max(w.end for w in windows)
    classified = classify_stops(stops, bound, params.micro_stop_s)
    deltas = counter_deltas(samples, params.rollover_window)
    return [compute_oee(w, classified, deltas, gaps) for w in windows]


def compute_oee_grouped(
    frm: datetime,
    to: datetime,
    group_by: GroupBy,
    plan: ShiftPlan,
    stops: Sequence[StopRecord],
    samples: Sequence[CounterSample],
    gaps: Sequence[Gap],
    params: OeeParams = DEFAULT_PARAMS,
    until: datetime | None = None,
) -> list[OeeResult]:
    """One result per shift or production day overlapping [frm, to), clipped to it."""
    windows = make_windows(frm, to, group_by, plan)
    return compute_oee_windows(windows, stops, samples, gaps, params, until)


def combine(results: Sequence[OeeResult], label: str = "total") -> OeeResult:
    """OEE of several windows together: sum times and parts, then recompute the ratios.

    Averaging the windows' ratios would be wrong (a short shift would weigh as much as a long
    one); this keeps the reference invariant true for any grouping.
    """
    if not results:
        raise ValueError("nothing to combine")
    window = Window(label, min(r.window.start for r in results), max(r.window.end for r in results))
    planned_s = sum(r.planned_s for r in results)
    operating_s = sum(r.operating_s for r in results)
    good = sum(r.good for r in results)
    scrap = sum(r.scrap for r in results)
    good_tct_s = sum(r.good_tct_s for r in results)
    total_tct_s = sum(r.total_tct_s for r in results)
    availability, performance, quality, oee = _ratios(
        planned_s, operating_s, good_tct_s, total_tct_s
    )
    anomalies = tuple(sorted({a for r in results for a in r.anomalies}))
    return OeeResult(
        window=window,
        gap_s=sum(r.gap_s for r in results),
        opening_s=sum(r.opening_s for r in results),
        planned_stop_s=sum(r.planned_stop_s for r in results),
        planned_s=planned_s,
        fault_s=sum(r.fault_s for r in results),
        setup_s=sum(r.setup_s for r in results),
        starved_s=sum(r.starved_s for r in results),
        blocked_s=sum(r.blocked_s for r in results),
        manual_s=sum(r.manual_s for r in results),
        micro_s=sum(r.micro_s for r in results),
        operating_s=operating_s,
        good=good,
        scrap=scrap,
        good_tct_s=good_tct_s,
        total_tct_s=total_tct_s,
        dropped_parts=sum(r.dropped_parts for r in results),
        availability=availability,
        performance=performance,
        quality=quality,
        oee=oee,
        anomalies=anomalies,
    )


def check_invariant(result: OeeResult, rel_tol: float = 1e-9) -> None:
    """Verify the accounting identities; raises ``OeeInvariantError`` on any violation.

    * time:  window = gaps + planned stops + losses + operating time
    * OEE == good parts x theoretical cycle time / planned time      (reference invariant)
    * OEE == A x P x Q whenever the three are defined
    """
    accounted = result.gap_s + result.planned_stop_s + result.loss_s + result.operating_s
    if not math.isclose(accounted, result.window.length_s, rel_tol=rel_tol, abs_tol=1e-6):
        raise OeeInvariantError(
            f"time accounting broken: {accounted} s accounted "
            f"for a {result.window.length_s} s window"
        )
    if result.planned_s <= 0:
        if any(
            x is not None
            for x in (result.availability, result.performance, result.quality, result.oee)
        ):
            raise OeeInvariantError("ratios must be None without planned time")
        return
    reference = result.good_tct_s / result.planned_s
    if result.oee is None or not math.isclose(
        result.oee, reference, rel_tol=rel_tol, abs_tol=1e-12
    ):
        raise OeeInvariantError(f"OEE {result.oee} != good x TCT / planned = {reference}")
    a, p, q = result.availability, result.performance, result.quality
    if (
        a is not None
        and p is not None
        and q is not None
        and not math.isclose(a * p * q, result.oee, rel_tol=rel_tol, abs_tol=1e-12)
    ):
        raise OeeInvariantError("A x P x Q differs from OEE")
