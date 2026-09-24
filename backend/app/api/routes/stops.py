"""GET /api/stops, PATCH /api/stops/{id}, GET /api/stops/pareto."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import Settings
from app.db.repo import (
    data_bounds,
    fetch_pareto_inputs,
    fetch_qualifiable_stops,
    fetch_stop,
    qualify_stop,
)
from app.domain import CAUSES
from app.oee import Window, compute_pareto

from ..deps import get_conn, get_settings, get_write_conn
from ..schemas import ParetoOut, QualifyIn, StopOut
from ..serialize import pareto_out, stop_out
from .common import TimeRange, time_range

router = APIRouter()

PLANNED_STOP = 2


@router.get("/api/stops")
async def list_stops(
    unqualified: bool = True,
    frm: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None, alias="to"),
    conn: AsyncConnection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> list[StopOut]:
    """Stops qualifiable by an operator: not planned, at least ``oee.micro_stop_s`` long
    (the open stop included, as soon as it reaches that duration)."""
    for name, v in (("from", frm), ("to", to)):
        if v is not None and v.tzinfo is None:
            raise HTTPException(422, f"'{name}' must include a UTC offset")
    _earliest, now = await data_bounds(conn)
    if now is None:
        return []
    rows = await fetch_qualifiable_stops(
        conn,
        now=now,
        min_duration_s=settings.oee.micro_stop_s,
        unqualified_only=unqualified,
        frm=frm.astimezone(UTC) if frm else None,
        to=to.astimezone(UTC) if to else None,
    )
    return [stop_out(r, now) for r in rows]


@router.patch("/api/stops/{stop_id}")
async def qualify(
    stop_id: int,
    body: QualifyIn,
    conn: AsyncConnection = Depends(get_write_conn),
    settings: Settings = Depends(get_settings),
) -> StopOut:
    if body.cause not in CAUSES:
        raise HTTPException(422, f"unknown cause {body.cause!r}; must be one of {sorted(CAUSES)}")
    row = await fetch_stop(conn, stop_id)
    if row is None:
        raise HTTPException(404, "stop not found")
    if row.state == PLANNED_STOP:
        raise HTTPException(409, "a planned stop cannot be qualified")
    _earliest, now = await data_bounds(conn)
    assert now is not None  # the stop we just fetched proves something has been observed
    duration = ((row.end_ts or now) - row.start_ts).total_seconds()
    if duration < settings.oee.micro_stop_s:
        raise HTTPException(409, "a micro-stop cannot be qualified")
    updated = await qualify_stop(conn, stop_id, body.cause, now)
    assert updated is not None
    return stop_out(updated, now)


@router.get("/api/stops/pareto")
async def pareto(
    range: TimeRange = Depends(time_range),
    by: Literal["cause", "station", "fault_code"] = "cause",
    metric: Literal["duration", "count"] = "duration",
    conn: AsyncConnection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> ParetoOut:
    _earliest, latest = await data_bounds(conn)
    until = min(range.to, latest) if latest else range.to
    inputs = await fetch_pareto_inputs(conn, range.frm, range.to)
    result = compute_pareto(
        inputs.facts,
        Window("range", range.frm, range.to),
        inputs.gaps,
        by,
        metric,
        settings.oee.micro_stop_s,
        until,
    )
    return pareto_out(result)
