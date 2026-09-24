"""Domain vocabulary shared by the collector, the database and the API.

Mirrors the simulator's address space (states, fault codes, stop origins); a real PLC would be
described the same way in the collector's configuration.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


class CellState(IntEnum):
    PRODUCING = 1
    PLANNED_STOP = 2
    FAULT = 3
    SETUP = 4
    STARVED = 5
    BLOCKED = 6
    MANUAL = 7


class StopOrigin(IntEnum):
    NONE = 0
    OP10 = 10
    OP20 = 20
    OP30 = 30
    CELL = 99


SCOPES: tuple[str, ...] = ("CELL", "OP10", "OP20", "OP30")
ORIGIN_STATIONS: tuple[str, ...] = ("OP10", "OP20", "OP30", "CELL")

STATION_OF_ORIGIN: dict[int, str] = {
    StopOrigin.OP10: "OP10",
    StopOrigin.OP20: "OP20",
    StopOrigin.OP30: "OP30",
    StopOrigin.CELL: "CELL",
}


class Cause(StrEnum):
    """Operator qualification causes (French labels live in the UI)."""

    MECHANICAL_BREAKDOWN = "mechanical_breakdown"
    ELECTRICAL_BREAKDOWN = "electrical_breakdown"
    SETUP_ADJUSTMENT = "setup_adjustment"
    TOOL_CHANGE = "tool_change"
    MISSING_PARTS = "missing_parts"
    DOWNSTREAM_SATURATION = "downstream_saturation"
    QUALITY_ISSUE = "quality_issue"
    OTHER = "other"


CAUSES: tuple[str, ...] = tuple(c.value for c in Cause)

_CAUSE_BY_FAULT: dict[int, Cause] = {
    101: Cause.MISSING_PARTS,
    102: Cause.MECHANICAL_BREAKDOWN,
    103: Cause.OTHER,
    201: Cause.QUALITY_ISSUE,
    202: Cause.ELECTRICAL_BREAKDOWN,
    203: Cause.MECHANICAL_BREAKDOWN,
    204: Cause.MECHANICAL_BREAKDOWN,
    301: Cause.QUALITY_ISSUE,
    302: Cause.ELECTRICAL_BREAKDOWN,
    303: Cause.MECHANICAL_BREAKDOWN,
    900: Cause.OTHER,
}

_CAUSE_BY_STATE: dict[CellState, Cause] = {
    CellState.SETUP: Cause.TOOL_CHANGE,
    CellState.STARVED: Cause.MISSING_PARTS,
    CellState.BLOCKED: Cause.DOWNSTREAM_SATURATION,
    CellState.MANUAL: Cause.OTHER,
}


def suggest_cause(state: int, fault_code: int = 0) -> Cause | None:
    """Pre-filled qualification for a stop; None for planned stops (nothing to qualify)."""
    if state == CellState.FAULT:
        return _CAUSE_BY_FAULT.get(fault_code, Cause.OTHER)
    return _CAUSE_BY_STATE.get(CellState(state))
