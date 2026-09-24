"""Domain vocabulary: states, fault catalogue and the OPC UA tag list."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum, StrEnum


class CellState(IntEnum):
    PRODUCING = 1
    PLANNED_STOP = 2
    FAULT = 3
    SETUP = 4
    STARVED = 5
    BLOCKED = 6
    MANUAL = 7


class Mode(IntEnum):
    AUTO = 1
    MANUAL = 2


class Station(StrEnum):
    CELL = "CELL"
    OP10 = "OP10"
    OP20 = "OP20"
    OP30 = "OP30"


@dataclass(frozen=True, slots=True)
class FaultDef:
    code: int
    station: Station
    meaning: str


FAULTS: dict[int, FaultDef] = {
    f.code: f
    for f in (
        FaultDef(101, Station.OP10, "Part missing (presence sensor)"),
        FaultDef(102, Station.OP10, "Clamp not in position"),
        FaultDef(103, Station.OP10, "Light curtain interrupted"),
        FaultDef(201, Station.OP20, "Weld current out of tolerance"),
        FaultDef(202, Station.OP20, "Robot fault (stopped on error)"),
        FaultDef(203, Station.OP20, "Gun cooling water flow fault"),
        FaultDef(204, Station.OP20, "Tip dresser fault"),
        FaultDef(301, Station.OP30, "Clip missing / screwing NOK"),
        FaultDef(302, Station.OP30, "Vision system fault"),
        FaultDef(303, Station.OP30, "Outfeed jam"),
        FaultDef(900, Station.CELL, "Emergency stop"),
    )
}

# Fault 201 is wear-driven (raised by the weld quality logic), not by an MTBF.
WELD_FAULT_CODE = 201
MTBF_FAULT_CODES: tuple[int, ...] = tuple(c for c in FAULTS if c != WELD_FAULT_CODE)

STATIONS: tuple[Station, ...] = (Station.OP10, Station.OP20, Station.OP30)


class StopOrigin(IntEnum):
    """Where a stop comes from (``Cell/StopOrigin``); 0 while producing."""

    NONE = 0
    OP10 = 10
    OP20 = 20
    OP30 = 30
    CELL = 99


_ORIGIN_OF_STATION = {
    Station.OP10: StopOrigin.OP10,
    Station.OP20: StopOrigin.OP20,
    Station.OP30: StopOrigin.OP30,
    Station.CELL: StopOrigin.CELL,
}


def fault_origin(fault_code: int) -> StopOrigin:
    return _ORIGIN_OF_STATION[FAULTS[fault_code].station]


def default_origin(state: CellState, fault_code: int = 0) -> StopOrigin:
    """Origin implied by the state. MANUAL is not implied: it inherits the fault it follows."""
    match state:
        case CellState.PRODUCING:
            return StopOrigin.NONE
        case CellState.PLANNED_STOP:
            return StopOrigin.CELL
        case CellState.FAULT:
            return fault_origin(fault_code)
        case CellState.SETUP:  # tip dressing and cap change happen at the welding station
            return StopOrigin.OP20
        case CellState.STARVED:  # no incoming part
            return StopOrigin.OP10
        case CellState.BLOCKED:  # outfeed full
            return StopOrigin.OP30
        case CellState.MANUAL:
            raise ValueError("MANUAL has no implied origin")


TagValue = int | float | bool


class TagType(StrEnum):
    INT16 = "Int16"
    UINT32 = "UInt32"
    FLOAT = "Float"
    BOOLEAN = "Boolean"


# Address space, as browsed under the PLC's OPC UA server: "<folder>/<variable>".
TAGS: dict[str, TagType] = {
    "Cell/State": TagType.INT16,
    "Cell/Mode": TagType.INT16,
    "Cell/FaultCode": TagType.INT16,
    "Cell/StopOrigin": TagType.INT16,
    "Cell/GoodCount": TagType.UINT32,
    "Cell/ScrapCount": TagType.UINT32,
    "Cell/LastCycleTime": TagType.FLOAT,
    "Cell/TheoreticalCycleTime": TagType.FLOAT,
    "Cell/Heartbeat": TagType.UINT32,
    "OP10/State": TagType.INT16,
    "OP10/FaultCode": TagType.INT16,
    "OP20/State": TagType.INT16,
    "OP20/FaultCode": TagType.INT16,
    "OP30/State": TagType.INT16,
    "OP30/FaultCode": TagType.INT16,
    "OP20/Weld/LastPointCurrent": TagType.FLOAT,
    "OP20/Weld/PointsSinceCapChange": TagType.UINT32,
    "OP20/Weld/LastPointOK": TagType.BOOLEAN,
}

UINT32_MASK = 0xFFFFFFFF


@dataclass(frozen=True, slots=True)
class TagChange:
    """A tag write, stamped with the *simulated* time (POSIX seconds, UTC)."""

    ts: float
    path: str
    value: TagValue


def to_datetime(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=UTC)
