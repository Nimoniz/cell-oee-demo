"""GET /api/cell/live, GET /api/cell/timeline and WS /ws/live."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import Settings
from app.db.repo import data_bounds
from app.service import current_shift_start, shift_timeline

from ..deps import get_conn, get_hub, get_settings
from ..live_hub import LiveHub
from ..schemas import LiveOut, TimelineOut
from ..serialize import timeline_out

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/cell/live")
async def get_live(hub: LiveHub = Depends(get_hub)) -> LiveOut:
    return await hub.snapshot()


@router.get("/api/cell/timeline")
async def get_timeline(
    conn: AsyncConnection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> TimelineOut:
    """State history of the current shift, from its start to now — for the live andon bar.

    Polled by the front (it is not pushed over the WebSocket: a shift's timeline changes only
    a few times a minute, not worth a place in the 4 Hz live payload).
    """
    _earliest, now = await data_bounds(conn)
    if now is None:
        return TimelineOut(window_start=None, now=None, segments=[])
    segments = await shift_timeline(conn, settings.oee, now)
    window_start = current_shift_start(now, settings.oee)
    return timeline_out(segments, window_start, now)


@router.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    hub: LiveHub = websocket.app.state.hub
    await websocket.accept()
    queue = hub.subscribe()
    try:
        while True:
            message = await queue.get()
            await websocket.send_json(message.model_dump())
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(queue)
