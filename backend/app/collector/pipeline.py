"""The collector: OPC UA changes -> assembler -> tracker/watchdog -> store.

Everything is ordered and stored by SourceTimestamp. Wall-clock time is used for exactly three
things: how long to wait for stragglers (assembler), how long the heartbeat may stay silent
(watchdog), and how often to flush progress (``last_seen_ts``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings

from .assembler import ChangeAssembler
from .opcua_source import OpcUaSource
from .store import CollectorStore
from .tracker import StopTracker
from .types import HEARTBEAT, Op, RawChange
from .watchdog import HeartbeatWatchdog

log = logging.getLogger(__name__)

_TICK_S = 0.25


class Collector:
    def __init__(
        self,
        settings: Settings,
        engine: AsyncEngine,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._store = CollectorStore(engine, notify_channel=settings.notify_channel)
        self._clock = clock
        self._queue: asyncio.Queue[RawChange] = asyncio.Queue()
        self._lock = asyncio.Lock()  # one transaction at a time: consumer and ticker both write
        self._max_ts: datetime | None = None
        self._written_ts: datetime | None = None
        self._last_flush = 0.0
        self.source: OpcUaSource | None = None

    async def run(self, stop: asyncio.Event) -> None:
        cfg = self._settings.collector
        resume = await self._store.resume()
        self._tracker = StopTracker(resume.tracker)
        self._assembler = ChangeAssembler(cfg.assembler_settle_s)
        self._watchdog = HeartbeatWatchdog(
            self._settings.heartbeat_timeout_s,
            now=self._clock(),
            jump_s=cfg.heartbeat_sim_s,
            last_seen_ts=resume.last_seen_ts,
            open_gap_start=resume.open_gap_start,
        )
        self._max_ts = self._written_ts = resume.last_seen_ts
        self._first_run = resume.last_seen_ts is None
        self.source = OpcUaSource(
            cfg, self._queue, lambda: self._watchdog.on_connected(self._clock())
        )
        log.info(
            "collector starting: last seen %s, open gap %s, heartbeat timeout %.1f s",
            resume.last_seen_ts,
            resume.open_gap_start,
            self._settings.heartbeat_timeout_s,
        )
        tasks = [
            asyncio.create_task(self.source.run(stop), name="opcua"),
            asyncio.create_task(self._consume(stop), name="consume"),
            asyncio.create_task(self._tick(stop), name="tick"),
        ]
        stop_task = asyncio.create_task(stop.wait())
        try:
            done, _ = await asyncio.wait({stop_task, *tasks}, return_when=asyncio.FIRST_COMPLETED)
            for task in done - {stop_task}:
                if task.exception() is not None:  # a dead worker must not leave a zombie collector
                    raise task.exception()  # type: ignore[misc]
        finally:
            for task in [stop_task, *tasks]:
                task.cancel()
            await asyncio.gather(stop_task, *tasks, return_exceptions=True)
            if stop.is_set():  # a clean stop flushes; a crash (or cancellation) must not
                with contextlib.suppress(Exception):
                    await self._drain_on_shutdown()

    # ------------------------------------------------------------------ processing

    async def _consume(self, stop: asyncio.Event) -> None:
        while True:
            first = await self._queue.get()
            batch = [first]
            while not self._queue.empty():
                batch.append(self._queue.get_nowait())
            await self._process(batch)

    async def _process(self, batch: list[RawChange]) -> None:
        now = self._clock()
        ops: list[Op] = []
        if self._first_run:
            self._watchdog.seed(min(c.ts for c in batch))
            self._first_run = False
        beats = sorted((c for c in batch if c.tag == HEARTBEAT), key=lambda c: c.ts)
        for c in beats:  # heartbeats bypass the assembler: their arrival time is the signal
            ops += self._watchdog.on_heartbeat(c.ts, now)
        rest = [c for c in batch if c.tag != HEARTBEAT]
        for group in self._assembler.feed(rest, now):
            ops += self._tracker.apply(group)
        newest = max(c.ts for c in batch)
        self._max_ts = newest if self._max_ts is None else max(self._max_ts, newest)
        await self._write(ops, now)

    async def _tick(self, stop: asyncio.Event) -> None:
        health = Path(self._settings.collector.health_file)
        while True:
            await asyncio.sleep(_TICK_S)
            now = self._clock()
            ops: list[Op] = []
            for group in self._assembler.poll(now):
                ops += self._tracker.apply(group)
            ops += self._watchdog.poll(now)
            await self._write(ops, now)
            with contextlib.suppress(OSError):
                health.write_text(str(time.time()))

    async def _drain_on_shutdown(self) -> None:
        ops: list[Op] = []
        while not self._queue.empty():
            self._queue.get_nowait()  # the rest is covered by the gap after restart
        for group in self._assembler.flush():
            ops += self._tracker.apply(group)
        await self._write(ops, self._clock(), force=True)

    def _safe_last_seen(self) -> datetime | None:
        """Latest simulated time that is fully processed.

        Never past the oldest change still waiting in the assembler: if we crash, everything after
        ``last_seen_ts`` is treated as unobserved (a gap), so it must not include unwritten events.
        """
        if self._max_ts is None:
            return None
        oldest = self._assembler.oldest_pending
        if oldest is not None:
            return min(self._max_ts, oldest - timedelta(microseconds=1))
        return self._max_ts

    async def _write(self, ops: list[Op], now: float, force: bool = False) -> None:
        cfg = self._settings.collector
        last_seen = self._safe_last_seen()
        due = now - self._last_flush >= cfg.last_seen_flush_s
        fresh = last_seen is not None and (self._written_ts is None or last_seen > self._written_ts)
        write_seen = last_seen if (fresh and (ops or due or force)) else None
        if not ops and write_seen is None:
            return
        async with self._lock:
            await self._store.apply(ops, write_seen)
        if write_seen is not None:
            self._written_ts = write_seen
            self._last_flush = now
