"""GET /api/weld?from=&to= — weld current and scrap rate, bucketed in time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import Settings
from app.db.repo import data_bounds, fetch_pareto_inputs, fetch_weld_inputs
from app.oee import choose_bucket_s, compute_weld_buckets, counter_deltas

from ..deps import get_conn, get_settings
from ..schemas import WeldMarkerOut, WeldOut
from ..serialize import iso_z, weld_out
from .common import TimeRange, time_range

router = APIRouter()

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
SETUP = 4


def _day_origin(day_start_s: int) -> datetime:
    """A fixed reference at the configured shift-day start, so bucket grids never drift with
    the requested range (a later query sees the exact same grid, just a different slice)."""
    return _EPOCH + timedelta(seconds=day_start_s)


@router.get("/api/weld")
async def get_weld(
    range: TimeRange = Depends(time_range),
    conn: AsyncConnection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> WeldOut:
    _earliest, latest = await data_bounds(conn)
    bound = min(range.to, latest) if latest else range.to

    bucket_s = choose_bucket_s(
        (range.to - range.frm).total_seconds(), settings.api.weld_target_buckets
    )
    origin = _day_origin(settings.oee.plan.day_start_s)

    weld = await fetch_weld_inputs(conn, range.frm, bound)
    deltas = counter_deltas(weld.samples, settings.oee.rollover_window)
    buckets = compute_weld_buckets(
        weld.points, deltas, weld.gaps, range.frm, range.to, bucket_s, origin
    )

    pareto_inputs = await fetch_pareto_inputs(conn, range.frm, range.to)
    markers = [
        WeldMarkerOut(
            ts=iso_z(f.start),
            kind="dressing"
            if (f.end or bound) - f.start < timedelta(seconds=settings.oee.micro_stop_s)
            else "cap_change",
        )
        for f in pareto_inputs.facts
        if f.state == SETUP
    ]

    return weld_out(
        buckets, markers, bucket_s, settings.api.weld_nominal_ka, settings.api.weld_tolerance_pct
    )
