"""Plain data types of the OEE core. No I/O, no database, no wall-clock time.

All instants are timezone-aware ``datetime`` in simulated time (UTC). Naive datetimes are
rejected: mixing them up with local time is the classic way to get an OEE wrong by an hour.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


def _require_aware(**instants: datetime | None) -> None:
    for name, value in instants.items():
        if value is not None and value.tzinfo is None:
            raise ValueError(f"{name} must be timezone-aware")


class StopKind(StrEnum):
    PLANNED = "planned"  # removed from planned time
    LOSS = "loss"  # availability loss
    MICRO = "micro"  # shorter than the threshold: stays inside operating time


class DeltaKind(StrEnum):
    FIRST = "first"  # first sample: no baseline, no production attributed
    NORMAL = "normal"
    ROLLOVER = "rollover"  # 32-bit counter wrapped around
    RESET = "reset"  # PLC restart: production during the reset is unknown, counted as 0


@dataclass(frozen=True, slots=True)
class Window:
    """Half-open period [start, end) over which OEE is computed."""

    label: str
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _require_aware(start=self.start, end=self.end)
        if self.end < self.start:
            raise ValueError("window end before start")

    @property
    def length_s(self) -> float:
        return (self.end - self.start).total_seconds()


@dataclass(frozen=True, slots=True)
class StopRecord:
    """A non-producing period as stored: ``end`` is None while the stop is still open."""

    start: datetime
    end: datetime | None
    state: int  # CellState value, PRODUCING excluded

    def __post_init__(self) -> None:
        _require_aware(start=self.start, end=self.end)


@dataclass(frozen=True, slots=True)
class ClassifiedStop:
    start: datetime
    end: datetime  # open stops are bounded at ``until``
    state: int
    kind: StopKind

    @property
    def duration_s(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def is_induced(self) -> bool:
        return self.state in (5, 6)  # STARVED, BLOCKED


@dataclass(frozen=True, slots=True)
class CounterSample:
    """Raw cumulative counters as read from the PLC."""

    ts: datetime
    good_total: int
    scrap_total: int
    theoretical_cycle_s: float

    def __post_init__(self) -> None:
        _require_aware(ts=self.ts)


@dataclass(frozen=True, slots=True)
class CounterDelta:
    """Parts produced between two consecutive samples, attributed to the later one's instant."""

    ts: datetime
    prev_ts: datetime | None
    good: int
    scrap: int
    theoretical_cycle_s: float
    kind: DeltaKind

    @property
    def parts(self) -> int:
        return self.good + self.scrap


@dataclass(frozen=True, slots=True)
class Gap:
    """Communication loss: nothing was observed, so nothing is counted."""

    start: datetime
    end: datetime | None  # None while the gap is still open

    def __post_init__(self) -> None:
        _require_aware(start=self.start, end=self.end)


@dataclass(frozen=True, slots=True)
class OeeParams:
    micro_stop_s: float = 120.0
    rollover_window: int = 1000


DEFAULT_PARAMS = OeeParams()


@dataclass(frozen=True, slots=True)
class ShiftDef:
    name: str
    start_s: int  # seconds since midnight
    end_s: int  # may be <= start_s: the shift crosses midnight


@dataclass(frozen=True, slots=True)
class ShiftPlan:
    shifts: tuple[ShiftDef, ...]
    day_start_s: int  # production day starts here (the first shift's start)


@dataclass(frozen=True, slots=True)
class OeeResult:
    """OEE of one window, with the full time and parts accounting behind it.

    Times are in seconds. ``availability``, ``performance``, ``quality`` and ``oee`` are None
    when undefined: everything is None if there was no planned time; a single ratio is None if
    its own denominator is zero.
    """

    window: Window
    gap_s: float  # communication loss, excluded from everything
    opening_s: float  # window minus gaps
    planned_stop_s: float
    planned_s: float  # opening minus planned stops
    fault_s: float
    setup_s: float
    starved_s: float
    blocked_s: float
    manual_s: float
    micro_s: float  # informational: stays inside operating time
    operating_s: float
    good: int
    scrap: int
    good_tct_s: float  # sum of good parts x theoretical cycle time
    total_tct_s: float  # sum of all parts x theoretical cycle time
    dropped_parts: int  # produced during a communication gap: not counted
    availability: float | None
    performance: float | None
    quality: float | None
    oee: float | None
    anomalies: tuple[str, ...] = ()

    @property
    def total_parts(self) -> int:
        return self.good + self.scrap

    @property
    def loss_s(self) -> float:
        return self.fault_s + self.setup_s + self.starved_s + self.blocked_s + self.manual_s

    @property
    def induced_s(self) -> float:
        return self.starved_s + self.blocked_s
