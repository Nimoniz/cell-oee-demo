"""The live view's state: fed by PostgreSQL LISTEN/NOTIFY, broadcast to WebSocket clients.

Two independent loops, both throttled in *wall-clock* time regardless of ACCELERATION or the
notification rate:

* a cheap "fast" refresh (state, counters, weld, open stop) at ``ws_publish_interval_ms`` — this
  is what every connected client receives, at most 4 messages/s each;
* an "OEE" refresh at ``oee_refresh_interval_s``, computed once and shared by every client.

``now`` is the hub's own idea of live time. It only ever moves forward, and it is fed *solely* by
NOTIFY payloads (never by re-reading ``collector_state.last_seen_ts``), per the rule that the
live view's "now" is the latest timestamp received over the stream, not what the database
reports. LISTEN/NOTIFY is a wake-up hint only, not a data channel: on (re)connection and
periodically as a safety net, the hub still re-reads the database — the payload just makes it do
so sooner. (The very first refresh, before any NOTIFY has ever arrived, does seed ``now`` from
the database once — otherwise the live view could never start.)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime

import psycopg
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.db.repo import fetch_live_snapshot
from app.service import live_oee

from .schemas import LiveOut
from .serialize import live_out

log = logging.getLogger(__name__)

_POLL_FALLBACK_S = 5.0  # safety net if a NOTIFY is ever missed or the listener is reconnecting


def _psycopg_dsn(database_url: str) -> str:
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


class LiveHub:
    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
        self._engine = engine
        self._settings = settings
        self.now: datetime | None = None
        self._stops_rev = 0
        self._last_open_stop_id: int | None = None
        self._latest_message: LiveOut | None = None
        self._oee_cache = None
        self._clients: set[asyncio.Queue[LiveOut]] = set()
        self._dirty = asyncio.Event()
        self._dirty.set()

    # ------------------------------------------------------------------ client management

    def subscribe(self) -> asyncio.Queue[LiveOut]:
        q: asyncio.Queue[LiveOut] = asyncio.Queue(maxsize=1)
        if self._latest_message is not None:
            q.put_nowait(self._latest_message)
        self._clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[LiveOut]) -> None:
        self._clients.discard(q)

    async def snapshot(self) -> LiveOut:
        """The latest broadcast message, computing one on the spot if none exists yet."""
        if self._latest_message is None:
            await self._refresh()
        assert self._latest_message is not None
        return self._latest_message

    def _publish(self, message: LiveOut) -> None:
        self._latest_message = message
        for q in self._clients:
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()  # the newest snapshot always wins over a stale one
            q.put_nowait(message)

    # ------------------------------------------------------------------ listener

    def _on_notify(self, payload: str) -> None:
        try:
            data = json.loads(payload)
        except ValueError:
            data = {}
        now_str = data.get("now")
        if now_str:
            ts = datetime.fromisoformat(now_str)
            if self.now is None or ts > self.now:
                self.now = ts
        self._dirty.set()

    async def _listen(self, stop: asyncio.Event) -> None:
        dsn = _psycopg_dsn(self._settings.database_url)
        channel = self._settings.notify_channel
        backoff = 0.5
        while not stop.is_set():
            try:
                async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
                    await conn.execute(f"LISTEN {channel}")  # noqa: S608 (channel is our own config)
                    log.info("listening on %r", channel)
                    backoff = 0.5
                    self._dirty.set()  # catch up on anything missed while (re)connecting
                    async for note in conn.notifies():
                        self._on_notify(note.payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("LISTEN connection lost: %s (retry in %.1f s)", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)

    # ------------------------------------------------------------------ refresh loops

    async def _fast_loop(self, stop: asyncio.Event) -> None:
        interval = self._settings.api.ws_publish_interval_ms / 1000
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._dirty.wait(), timeout=_POLL_FALLBACK_S)
            self._dirty.clear()
            await self._refresh()
            await asyncio.sleep(interval)

    async def _refresh(self) -> None:
        async with self._engine.connect() as conn:
            snapshot = await fetch_live_snapshot(conn)
        if snapshot.now is not None and (self.now is None or snapshot.now > self.now):
            self.now = snapshot.now  # first refresh, or a NOTIFY carried no payload
        open_id = snapshot.open_stop.id if snapshot.open_stop else None
        if open_id != self._last_open_stop_id:
            self._stops_rev += 1
            self._last_open_stop_id = open_id
        message = live_out(
            snapshot,
            self._oee_cache,
            self._stops_rev,
            self.now,
            self._settings.api.live_min_required_s,
        )
        self._publish(message)

    async def _oee_loop(self, stop: asyncio.Event) -> None:
        interval = self._settings.api.oee_refresh_interval_s
        while not stop.is_set():
            if self.now is not None:
                try:
                    async with self._engine.connect() as conn:
                        self._oee_cache = await live_oee(conn, self._settings.oee, self.now)
                except Exception:  # noqa: BLE001
                    log.exception("live OEE refresh failed")
            await asyncio.sleep(interval)

    # ------------------------------------------------------------------ lifecycle

    async def run(self, stop: asyncio.Event) -> None:
        await self._refresh()  # an initial snapshot before the first client connects
        tasks = [
            asyncio.create_task(self._listen(stop), name="hub-listen"),
            asyncio.create_task(self._fast_loop(stop), name="hub-fast"),
            asyncio.create_task(self._oee_loop(stop), name="hub-oee"),
        ]
        try:
            await stop.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
