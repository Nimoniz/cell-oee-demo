"""Fault injection for exercising the collector (heartbeat loss, counter reset, rollover).

Periodic events are aligned on absolute simulated time, so they do not depend on when the
process was (re)started.
"""

from __future__ import annotations

from .cell import Cell, Host
from .config import ChaosCfg


class ChaosInjector:
    def __init__(self, cfg: ChaosCfg, host: Host, cell: Cell) -> None:
        self._cfg = cfg
        self._host = host
        self._cell = cell
        host.register("chaos_freeze_start", self._on_freeze_start)
        host.register("chaos_freeze_end", self._on_freeze_end)
        host.register("chaos_counter_reset", self._on_counter_reset)

    def apply_at_start(self) -> None:
        if self._cfg.enabled and self._cfg.counters_at_start is not None:
            c = self._cfg.counters_at_start
            self._cell.set_counters(c.good, c.scrap)

    def arm(self) -> None:
        if not self._cfg.enabled:
            return
        if self._cfg.heartbeat_freeze is not None:
            self._schedule_next("chaos_freeze_start", self._cfg.heartbeat_freeze.every_h)
        if self._cfg.counter_reset_every_h is not None:
            self._schedule_next("chaos_counter_reset", self._cfg.counter_reset_every_h)

    def _schedule_next(self, kind: str, every_h: float) -> None:
        period = every_h * 3600
        now = self._host.now
        next_ts = (now // period + 1) * period
        self._host.schedule(next_ts - now, kind)

    def _on_freeze_start(self, _payload: object, _epoch: int | None) -> None:
        assert self._cfg.heartbeat_freeze is not None
        self._cell.freeze_heartbeat(True)
        self._host.schedule(self._cfg.heartbeat_freeze.duration_s, "chaos_freeze_end")

    def _on_freeze_end(self, _payload: object, _epoch: int | None) -> None:
        assert self._cfg.heartbeat_freeze is not None
        self._cell.freeze_heartbeat(False)
        self._schedule_next("chaos_freeze_start", self._cfg.heartbeat_freeze.every_h)

    def _on_counter_reset(self, _payload: object, _epoch: int | None) -> None:
        assert self._cfg.counter_reset_every_h is not None
        self._cell.reset_plc()
        self._schedule_next("chaos_counter_reset", self._cfg.counter_reset_every_h)
