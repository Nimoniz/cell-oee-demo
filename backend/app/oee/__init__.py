"""Pure OEE logic: no I/O, no database, no wall-clock time."""

from .compute import (
    OeeInvariantError,
    check_invariant,
    combine,
    compute_oee,
    compute_oee_grouped,
    compute_oee_windows,
)
from .counters import counter_deltas
from .pareto import ParetoBucket, ParetoResult, StopFact, compute_pareto
from .stops import classify_stops
from .timeline import StateEvent, TimelineSegment, build_timeline
from .types import (
    ClassifiedStop,
    CounterDelta,
    CounterSample,
    DeltaKind,
    Gap,
    OeeParams,
    OeeResult,
    ShiftDef,
    ShiftPlan,
    StopKind,
    StopRecord,
    Window,
)
from .weld import WeldBucket, WeldPoint, bucket_start, choose_bucket_s, compute_weld_buckets
from .windows import GroupBy, day_windows, make_windows, shift_windows

__all__ = [
    "ClassifiedStop",
    "CounterDelta",
    "CounterSample",
    "DeltaKind",
    "Gap",
    "GroupBy",
    "OeeInvariantError",
    "OeeParams",
    "OeeResult",
    "ParetoBucket",
    "ParetoResult",
    "ShiftDef",
    "ShiftPlan",
    "StateEvent",
    "StopFact",
    "StopKind",
    "StopRecord",
    "TimelineSegment",
    "WeldBucket",
    "WeldPoint",
    "Window",
    "bucket_start",
    "build_timeline",
    "check_invariant",
    "choose_bucket_s",
    "classify_stops",
    "combine",
    "compute_oee",
    "compute_oee_grouped",
    "compute_oee_windows",
    "compute_pareto",
    "compute_weld_buckets",
    "counter_deltas",
    "day_windows",
    "make_windows",
    "shift_windows",
]
