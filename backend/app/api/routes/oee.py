"""GET /api/oee?from=&to=&group_by=shift|day"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import Settings
from app.service import oee_over_range

from ..deps import get_conn, get_settings
from ..schemas import OeeOut
from ..serialize import oee_out
from .common import TimeRange, time_range

router = APIRouter()


@router.get("/api/oee")
async def get_oee(
    range: TimeRange = Depends(time_range),
    group_by: Literal["shift", "day"] | None = None,
    conn: AsyncConnection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> list[OeeOut]:
    results = await oee_over_range(conn, settings.oee, range.frm, range.to, group_by)
    return [oee_out(r) for r in results]
