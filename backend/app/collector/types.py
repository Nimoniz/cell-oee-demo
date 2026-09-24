"""What flows through the collector: raw changes in, database operations out."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

TagValue = int | float | bool

HEARTBEAT = "Cell/Heartbeat"
CELL_STATE = "Cell/State"
CELL_FAULT = "Cell/FaultCode"
CELL_ORIGIN = "Cell/StopOrigin"
GOOD = "Cell/GoodCount"
SCRAP = "Cell/ScrapCount"
TCT = "Cell/TheoreticalCycleTime"
LAST_CYCLE = "Cell/LastCycleTime"
WELD_CURRENT = "OP20/Weld/LastPointCurrent"
WELD_POINTS = "OP20/Weld/PointsSinceCapChange"
WELD_OK = "OP20/Weld/LastPointOK"
STATIONS = ("OP10", "OP20", "OP30")

# Every tag of the address space. Tags that can change many times per second get the big queue.
FAST_TAGS = frozenset({HEARTBEAT, WELD_CURRENT, WELD_POINTS, WELD_OK})
ALL_TAGS: tuple[str, ...] = (
    CELL_STATE,
    "Cell/Mode",
    CELL_FAULT,
    CELL_ORIGIN,
    GOOD,
    SCRAP,
    LAST_CYCLE,
    TCT,
    HEARTBEAT,
    *(f"{st}/{leaf}" for st in STATIONS for leaf in ("State", "FaultCode")),
    WELD_CURRENT,
    WELD_POINTS,
    WELD_OK,
)


@dataclass(frozen=True, slots=True)
class RawChange:
    """One data-change notification. ``ts`` is the SourceTimestamp: simulated time."""

    tag: str
    value: TagValue
    ts: datetime


@dataclass(slots=True)
class Group:
    """All the tags that changed at one SourceTimestamp."""

    ts: datetime
    values: dict[str, TagValue] = field(default_factory=dict)
    late: bool = False  # older than something already released: cannot be reordered anymore


# ---------------------------------------------------------------- database operations


@dataclass(frozen=True, slots=True)
class StateEventOp:
    scope: str
    ts: datetime
    state: int
    fault_code: int
    stop_origin: int | None  # CELL scope only


@dataclass(frozen=True, slots=True)
class OpenStopOp:
    start_ts: datetime
    state: int
    origin_station: str
    fault_code: int
    suggested_category: str | None


@dataclass(frozen=True, slots=True)
class CloseStopOp:
    start_ts: datetime
    end_ts: datetime


@dataclass(frozen=True, slots=True)
class CounterSampleOp:
    ts: datetime
    good: int
    scrap: int
    theoretical_cycle_s: float
    last_cycle_s: float


@dataclass(frozen=True, slots=True)
class WeldSampleOp:
    ts: datetime
    current_ka: float
    points_since_cap_change: int
    ok: bool


@dataclass(frozen=True, slots=True)
class OpenGapOp:
    start_ts: datetime


@dataclass(frozen=True, slots=True)
class CloseGapOp:
    start_ts: datetime
    end_ts: datetime


@dataclass(frozen=True, slots=True)
class ClosedGapOp:
    start_ts: datetime
    end_ts: datetime


Op = (
    StateEventOp
    | OpenStopOp
    | CloseStopOp
    | CounterSampleOp
    | WeldSampleOp
    | OpenGapOp
    | CloseGapOp
    | ClosedGapOp
)
