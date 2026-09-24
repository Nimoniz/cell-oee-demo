"""OPC UA client: subscriptions (never polling), automatic reconnection with backoff."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import UTC

from asyncua import Client, ua

from app.config import CollectorCfg

from .types import ALL_TAGS, FAST_TAGS, RawChange

log = logging.getLogger(__name__)

# StatusCode "info bits": DataValue info type (0x0400) + Overflow (0x0080): the server dropped
# values from a full queue. Logged and counted; the heartbeat jump rule catches what matters.
_OVERFLOW_BITS = 0x0400 | 0x0080


class _Handler:
    def __init__(self, queue: asyncio.Queue[RawChange], on_overflow: Callable[[str], None]) -> None:
        self._queue = queue
        self._on_overflow = on_overflow

    def datachange_notification(self, node, val, data) -> None:  # noqa: ANN001
        value = data.monitored_item.Value
        source_ts = value.SourceTimestamp
        tag = str(node.nodeid.Identifier).replace(".", "/")
        if source_ts is None:
            # Never fall back to arrival time: without a source timestamp a value is unusable.
            log.warning("dropping %s: notification without SourceTimestamp", tag)
            return
        if value.StatusCode.value & _OVERFLOW_BITS == _OVERFLOW_BITS:
            self._on_overflow(tag)
        ts = source_ts.replace(tzinfo=UTC) if source_ts.tzinfo is None else source_ts
        self._queue.put_nowait(RawChange(tag, val, ts))

    def status_change_notification(self, status) -> None:  # noqa: ANN001
        log.warning("subscription status change: %s", status)


class OpcUaSource:
    def __init__(
        self,
        cfg: CollectorCfg,
        queue: asyncio.Queue[RawChange],
        on_connected: Callable[[], None] = lambda: None,
    ) -> None:
        self._cfg = cfg
        self._queue = queue
        self._on_connected = on_connected
        self.overflows = 0
        self.connected = False

    def _count_overflow(self, tag: str) -> None:
        self.overflows += 1
        log.warning("subscription queue overflow on %s: some changes were lost", tag)

    async def run(self, stop: asyncio.Event) -> None:
        """Connect, subscribe and stay connected; on any failure back off and start over."""
        backoff = self._cfg.reconnect_min_s
        while not stop.is_set():
            try:
                await self._session(stop)
                backoff = self._cfg.reconnect_min_s  # a session that worked resets the backoff
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("OPC UA session ended: %s (retry in %.1f s)", exc, backoff)
            self.connected = False
            if stop.is_set():
                break
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            backoff = min(backoff * 2, self._cfg.reconnect_max_s)

    async def _session(self, stop: asyncio.Event) -> None:
        cfg = self._cfg
        async with Client(cfg.opcua_endpoint, timeout=5) as client:
            idx = await client.get_namespace_index(cfg.namespace_uri)
            handler = _Handler(self._queue, self._count_overflow)
            sub = await client.create_subscription(cfg.publish_interval_ms, handler)
            for fast, size in ((True, cfg.queue_size_fast), (False, cfg.queue_size_events)):
                nodes = [
                    client.get_node(ua.NodeId(tag.replace("/", "."), idx))
                    for tag in ALL_TAGS
                    if (tag in FAST_TAGS) == fast
                ]
                await sub.subscribe_data_change(
                    nodes, queuesize=size, sampling_interval=cfg.sampling_interval_ms
                )
            self.connected = True
            self._on_connected()
            log.info("subscribed to %d tags on %s", len(ALL_TAGS), cfg.opcua_endpoint)
            while not stop.is_set():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=1.0)
                await client.check_connection()  # raises if the server went away
