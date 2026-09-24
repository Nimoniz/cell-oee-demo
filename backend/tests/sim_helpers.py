"""Use the real simulator (../simulator) as the source of truth in collector tests.

The simulator engine is deterministic: the same config and seed give the same tag changes, so
a run of the collector can be compared with an offline replay of the very same simulation.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

SIM_DIR = Path(__file__).resolve().parent.parent.parent / "simulator"
if str(SIM_DIR) not in sys.path:
    sys.path.insert(0, str(SIM_DIR))

from cell_sim.__main__ import serve  # noqa: E402,F401
from cell_sim.config import SimConfig  # noqa: E402
from cell_sim.engine import Simulation  # noqa: E402
from cell_sim.model import TagChange  # noqa: E402

from app.collector.types import Group, RawChange  # noqa: E402


def _deep_update(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    for key, value in overrides.items():
        if key != "faults" and isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
    return base


def sim_config(**overrides: Any) -> SimConfig:
    raw = yaml.safe_load((SIM_DIR / "config.yaml").read_text(encoding="utf-8"))
    raw["persistence"]["enabled"] = False
    return SimConfig.model_validate(_deep_update(raw, overrides))


def sim_changes(cfg: SimConfig, hours: float) -> list[TagChange]:
    """Every tag change of the first ``hours`` simulated hours, in order."""
    sim = Simulation(cfg)
    return sim.start() + sim.run_until(cfg.clock.start_ts + hours * 3600)


def dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=UTC)


def raw(changes: list[TagChange]) -> list[RawChange]:
    return [RawChange(c.path, c.value, dt(c.ts)) for c in changes]


def as_groups_ts(changes: list[RawChange]) -> list[Group]:
    """Changes grouped by SourceTimestamp, oldest first."""
    by_ts: dict[datetime, Group] = {}
    for c in changes:
        by_ts.setdefault(c.ts, Group(c.ts)).values[c.tag] = c.value
    return [by_ts[ts] for ts in sorted(by_ts)]
