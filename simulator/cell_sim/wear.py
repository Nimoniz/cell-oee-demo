"""Electrode wear: the signature behaviour of the cell.

Wear has two components, both dimensionless (1.0 ~ end of nominal cap life):

* ``reversible``   - mushrooming of the tip; tip dressing removes most of it;
* ``irreversible`` - material loss; only a cap change removes it (dressing even adds a bit).

Wear lowers the mean weld current (``drift``). A point is out of tolerance when the current
deviates from nominal by more than the tolerance; the deviation includes an autocorrelated
noise term (tip contamination is not white), which makes consecutive bad parts more likely
than pure chance and is what eventually raises fault 201.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from pydantic import BaseModel

from .config import WeldCfg


class WearState(BaseModel):
    """Retentive part of the model (persisted across restarts)."""

    reversible: float = 0.0
    irreversible: float = 0.0
    deviation: float = 0.0  # AR(1) state, as a fraction of nominal current
    points_since_cap_change: int = 0
    parts_since_cap_change: int = 0
    parts_since_dressing: int = 0


@dataclass(frozen=True, slots=True)
class WeldPoint:
    current_ka: float
    ok: bool


class ElectrodeWear:
    def __init__(self, cfg: WeldCfg, rng: random.Random, state: WearState | None = None) -> None:
        self._cfg = cfg
        self._rng = rng
        self.state = state.model_copy() if state else WearState()
        ppp = cfg.points_per_part
        wear = cfg.wear
        self._irr_per_point = wear.irreversible_at_cap_life / (cfg.cap_change.every_parts * ppp)
        self._rev_per_point = wear.reversible_at_dressing_interval / (
            cfg.tip_dressing.every_parts * ppp
        )
        rho = cfg.noise_autocorrelation
        self._rho = rho
        self._innovation = math.sqrt(1 - rho * rho) * cfg.noise_sigma_pct / 100

    @property
    def wear(self) -> float:
        return self.state.reversible + self.state.irreversible

    def drift(self) -> float:
        """Relative drop of the mean weld current (0.05 = -5 %)."""
        w = self._cfg.wear
        return w.drift_max_pct / 100 * self.wear**w.drift_exponent

    def weld_point(self) -> WeldPoint:
        s = self.state
        s.deviation = self._rho * s.deviation + self._innovation * self._rng.gauss(0.0, 1.0)
        rel = s.deviation - self.drift()
        ok = abs(rel) <= self._cfg.tolerance_pct / 100
        current = self._cfg.nominal_current_ka * (1 + rel)
        s.reversible += self._rev_per_point
        s.irreversible += self._irr_per_point
        s.points_since_cap_change += 1
        return WeldPoint(current, ok)

    def part_done(self) -> None:
        self.state.parts_since_cap_change += 1
        self.state.parts_since_dressing += 1

    def dress(self) -> None:
        w = self._cfg.wear
        self.state.reversible *= 1 - w.dressing_recovery
        self.state.irreversible += w.dressing_material_loss
        self.state.parts_since_dressing = 0

    def change_caps(self) -> None:
        self.state = WearState()
