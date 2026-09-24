from __future__ import annotations

import copy
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from cell_sim.config import SimConfig
from cell_sim.model import CellState, TagChange, TagValue

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"

HOUR = 3600.0
DAY = 86_400.0


# Mappings keyed by code: an override replaces them instead of merging into them.
REPLACED_KEYS = {"faults"}


def deep_update(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    for key, value in overrides.items():
        if key not in REPLACED_KEYS and isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_update(base[key], value)
        else:
            base[key] = value
    return base


def make_config(**overrides: Any) -> SimConfig:
    """The shipped config.yaml with nested overrides, e.g. ``make_config(cell={...})``."""
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    raw["persistence"]["enabled"] = False
    return SimConfig.model_validate(deep_update(copy.deepcopy(raw), overrides))


def quiet_config(**overrides: Any) -> SimConfig:
    """No breakdowns, no starved/blocked: only breaks, tip dressing and cap changes remain."""
    quiet: dict[str, Any] = {
        "faults": {},
        "flow": {
            "starved": {"rate_per_h": 0},
            "blocked": {"rate_per_h": 0},
        },
    }
    return make_config(**deep_update(quiet, overrides))


@dataclass(frozen=True)
class Segment:
    state: CellState
    start: float
    end: float
    fault_code: int

    @property
    def duration(self) -> float:
        return self.end - self.start


def segments(changes: list[TagChange], end_ts: float) -> list[Segment]:
    """Cell state timeline reconstructed from the tag changes."""
    out: list[Segment] = []
    state: int | None = None
    start = 0.0
    code = 0
    for ts, image in replay(changes):
        if image["Cell/State"] != state:
            if state is not None:
                out.append(Segment(CellState(state), start, ts, code))
            state, start, code = int(image["Cell/State"]), ts, int(image["Cell/FaultCode"])
    if state is not None:
        out.append(Segment(CellState(state), start, end_ts, code))
    return out


def replay(changes: list[TagChange]) -> Iterator[tuple[float, dict[str, TagValue]]]:
    """Yield the full tag image after each simulated timestamp."""
    image: dict[str, TagValue] = {}
    ts: float | None = None
    for c in changes:
        if ts is not None and c.ts != ts:
            yield ts, dict(image)
        ts = c.ts
        image[c.path] = c.value
    if ts is not None:
        yield ts, dict(image)


@pytest.fixture
def cfg() -> SimConfig:
    return make_config()
