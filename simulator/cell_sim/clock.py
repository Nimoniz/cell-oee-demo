"""Simulated clock: maps wall-clock time to simulated time with an acceleration factor."""

from __future__ import annotations

import time
from collections.abc import Callable


class SimClock:
    def __init__(
        self,
        start_ts: float,
        acceleration: float,
        wall: Callable[[], float] = time.monotonic,
    ) -> None:
        self._start_ts = start_ts
        self._acceleration = acceleration
        self._wall = wall
        self._wall0 = wall()

    @property
    def acceleration(self) -> float:
        return self._acceleration

    def now(self) -> float:
        """Current simulated time (POSIX seconds)."""
        return self._start_ts + (self._wall() - self._wall0) * self._acceleration

    def wall_delay(self, sim_ts: float) -> float:
        """Wall-clock seconds to wait until ``sim_ts``; 0 if already past (no catch-up sleep)."""
        return max(0.0, (sim_ts - self.now()) / self._acceleration)
