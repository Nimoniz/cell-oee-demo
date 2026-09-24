"""Disturbances of the cell: breakdowns, starved/blocked periods, manual recovery.

All hazards are measured in PRODUCING time. Exponential inter-arrivals are memoryless, so the
cell simply draws a fresh set of competing arrival times every time it resumes production.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Literal

from .config import Range, SimConfig, StopProcessCfg
from .rng import RngFactory

Kind = Literal["fault", "starved", "blocked"]


@dataclass(frozen=True, slots=True)
class Interruption:
    delay_s: float
    kind: Kind
    code: int = 0  # fault code, 0 for starved / blocked


def bounded_lognormal(rng: random.Random, bounds_min: Range) -> float:
    """Log-normal duration (seconds) whose ~95 % interval is the configured [lo, hi] minutes."""
    lo, hi = bounds_min[0] * 60, bounds_min[1] * 60
    mu = math.log(math.sqrt(lo * hi))
    sigma = math.log(hi / lo) / 4
    for _ in range(50):
        x = rng.lognormvariate(mu, sigma)
        if lo <= x <= hi:
            return x
    return min(max(math.exp(mu), lo), hi)


class DisturbanceModel:
    def __init__(self, cfg: SimConfig, rng: RngFactory) -> None:
        self._cfg = cfg
        self._arrivals = rng.stream("arrivals")
        self._durations = rng.stream("durations")
        self._manual = rng.stream("manual")

    def next_interruption(self) -> Interruption | None:
        """Earliest of the competing arrivals; None if nothing can go wrong (all hazards off)."""
        candidates: list[Interruption] = [
            Interruption(self._arrivals.expovariate(1 / (f.mtbf_min * 60)), "fault", code)
            for code, f in self._cfg.faults.items()
        ]
        for kind, proc in (
            ("starved", self._cfg.flow.starved),
            ("blocked", self._cfg.flow.blocked),
        ):
            if proc.rate_per_h > 0:
                candidates.append(
                    Interruption(self._arrivals.expovariate(proc.rate_per_h / 3600), kind)  # type: ignore[arg-type]
                )
        return min(candidates, key=lambda i: i.delay_s, default=None)

    def fault_duration_s(self, code: int) -> float:
        if code == 201:
            return bounded_lognormal(self._durations, self._cfg.weld.fault_201.duration_min)
        return bounded_lognormal(self._durations, self._cfg.faults[code].duration_min)

    def flow_duration_s(self, kind: Literal["starved", "blocked"]) -> float:
        proc: StopProcessCfg = getattr(self._cfg.flow, kind)
        if self._durations.random() < proc.p_short:
            return max(1.0, self._durations.expovariate(1 / proc.short_mean_s))
        return max(1.0, self._durations.expovariate(1 / (proc.long_mean_min * 60)))

    def manual_follows(self, fault_code: int) -> bool:
        m = self._cfg.manual
        return fault_code in m.after_faults and self._manual.random() < m.probability

    def manual_duration_s(self) -> float:
        return bounded_lognormal(self._manual, self._cfg.manual.duration_min)
