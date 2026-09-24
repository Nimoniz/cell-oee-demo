"""Validated configuration, loaded from ``config.yaml``."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .model import FAULTS, MTBF_FAULT_CODES

Range = tuple[float, float]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_range(value: Range) -> Range:
    lo, hi = value
    if not 0 < lo < hi:
        raise ValueError(f"range must satisfy 0 < lo < hi, got {value}")
    return value


class OpcUaCfg(_Strict):
    endpoint: str = "opc.tcp://0.0.0.0:4840"
    namespace_uri: str = "urn:cell-oee-demo:plc"


class ClockCfg(_Strict):
    # Simulated "plant time": no time zone, no DST. A naive value is read as UTC.
    start: dt.datetime
    acceleration: float = Field(default=1.0, ge=1.0, le=60.0)

    @field_validator("start")
    @classmethod
    def _to_utc(cls, v: dt.datetime) -> dt.datetime:
        return v.replace(tzinfo=dt.UTC) if v.tzinfo is None else v.astimezone(dt.UTC)

    @property
    def start_ts(self) -> float:
        return self.start.timestamp()


class ShiftCfg(_Strict):
    name: str
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
            raise ValueError(f"expected 'HH:MM', got {v!r}")
        return v

    @staticmethod
    def _seconds(hhmm: str) -> int:
        h, m = hhmm.split(":")
        return int(h) * 3600 + int(m) * 60

    @property
    def start_s(self) -> int:
        return self._seconds(self.start)

    @property
    def end_s(self) -> int:
        return self._seconds(self.end)


class PlannedBreakCfg(_Strict):
    offset_h: float = Field(default=4.0, gt=0)
    duration_min: float = Field(default=20.0, gt=0)


class CycleNoiseCfg(_Strict):
    """Extra time on top of the theoretical cycle: log-normal, always positive."""

    mean_extra_s: float = Field(default=1.5, gt=0)
    sigma: float = Field(default=0.6, gt=0)


class CellCfg(_Strict):
    theoretical_cycle_s: float = Field(default=55.0, gt=0)
    cycle_noise: CycleNoiseCfg = CycleNoiseCfg()
    base_scrap_rate: float = Field(default=0.015, ge=0, lt=1)


class WearCfg(_Strict):
    # Wear is dimensionless: 1.0 ~ end of nominal cap life.
    irreversible_at_cap_life: float = Field(gt=0)  # accumulated over one nominal cap life
    reversible_at_dressing_interval: float = Field(ge=0)  # accumulated between two dressings
    dressing_recovery: float = Field(ge=0, le=1)  # share of the reversible part removed
    dressing_material_loss: float = Field(ge=0)  # irreversible wear added per dressing
    drift_max_pct: float = Field(ge=0)  # current drop at wear == 1
    drift_exponent: float = Field(default=2.0, gt=0)


class TipDressingCfg(_Strict):
    every_parts: int = Field(default=150, gt=0)
    jitter_pct: float = Field(default=10.0, ge=0, lt=100)
    duration_s: float = Field(default=30.0, gt=0)


class CapChangeCfg(_Strict):
    every_parts: int = Field(default=1500, gt=0)
    jitter_pct: float = Field(default=5.0, ge=0, lt=100)
    duration_s: float = Field(default=300.0, gt=0)


class WeldFaultCfg(_Strict):
    """Fault 201: raised after N consecutive parts with a weld point out of tolerance."""

    consecutive_nok_parts: int = Field(default=2, ge=1)
    duration_min: Range = (5.0, 20.0)

    _range = field_validator("duration_min")(_check_range)


class WeldCfg(_Strict):
    points_per_part: int = Field(default=12, gt=0)
    nominal_current_ka: float = Field(default=8.0, gt=0)
    tolerance_pct: float = Field(default=10.0, gt=0)
    noise_sigma_pct: float = Field(default=2.0, ge=0)
    noise_autocorrelation: float = Field(default=0.9, ge=0, lt=1)
    wear: WearCfg
    tip_dressing: TipDressingCfg = TipDressingCfg()
    cap_change: CapChangeCfg = CapChangeCfg()
    fault_201: WeldFaultCfg = WeldFaultCfg()


class FaultCfg(_Strict):
    mtbf_min: float = Field(gt=0)  # measured in PRODUCING time
    duration_min: Range = (5.0, 45.0)

    _range = field_validator("duration_min")(_check_range)


class StopProcessCfg(_Strict):
    """Starved / blocked: Poisson arrivals in PRODUCING time, short/long duration mix."""

    rate_per_h: float = Field(ge=0)
    p_short: float = Field(ge=0, le=1)
    short_mean_s: float = Field(gt=0)
    long_mean_min: float = Field(gt=0)


class FlowCfg(_Strict):
    starved: StopProcessCfg
    blocked: StopProcessCfg


class ManualCfg(_Strict):
    after_faults: tuple[int, ...] = (202, 302, 303)
    probability: float = Field(default=0.5, ge=0, le=1)
    duration_min: Range = (2.0, 10.0)

    _range = field_validator("duration_min")(_check_range)


class PersistenceCfg(_Strict):
    """Retentive data, like a non-volatile DB on an S7-1500."""

    enabled: bool = True
    path: Path = Path("state/plc_state.json")
    save_interval_s: float = Field(default=5.0, gt=0)  # wall-clock seconds


class CountersCfg(_Strict):
    good: int = Field(default=0, ge=0, le=0xFFFFFFFF)
    scrap: int = Field(default=0, ge=0, le=0xFFFFFFFF)


class HeartbeatFreezeCfg(_Strict):
    every_h: float = Field(gt=0)
    duration_s: float = Field(gt=0)


class ChaosCfg(_Strict):
    """Fault injection for exercising the collector. Ignored unless ``enabled``."""

    enabled: bool = False
    # Overwrite the counters at every start: zeros = PLC restart, values near 2**32 = rollover.
    counters_at_start: CountersCfg | None = None
    counter_reset_every_h: float | None = Field(default=None, gt=0)
    heartbeat_freeze: HeartbeatFreezeCfg | None = None


class SimConfig(_Strict):
    seed: int = 42
    opcua: OpcUaCfg = OpcUaCfg()
    clock: ClockCfg
    shifts: tuple[ShiftCfg, ...]
    planned_break: PlannedBreakCfg = PlannedBreakCfg()
    cell: CellCfg = CellCfg()
    weld: WeldCfg
    faults: dict[int, FaultCfg]
    flow: FlowCfg
    manual: ManualCfg = ManualCfg()
    persistence: PersistenceCfg = PersistenceCfg()
    chaos: ChaosCfg = ChaosCfg()

    @field_validator("faults")
    @classmethod
    def _known_faults(cls, v: dict[int, FaultCfg]) -> dict[int, FaultCfg]:
        unknown = set(v) - set(MTBF_FAULT_CODES)
        if unknown:
            raise ValueError(
                f"unknown or wear-driven fault codes {sorted(unknown)}; "
                f"allowed: {list(MTBF_FAULT_CODES)}"
            )
        return v

    @field_validator("shifts")
    @classmethod
    def _contiguous_shifts(cls, v: tuple[ShiftCfg, ...]) -> tuple[ShiftCfg, ...]:
        if not v:
            raise ValueError("at least one shift is required")
        for cur, nxt in zip(v, v[1:] + v[:1], strict=True):
            if cur.end != nxt.start:
                raise ValueError(f"shift {cur.name!r} must end when {nxt.name!r} starts")
        return v

    @model_validator(mode="after")
    def _manual_codes_exist(self) -> SimConfig:
        bad = [c for c in self.manual.after_faults if c not in FAULTS]
        if bad:
            raise ValueError(f"manual.after_faults contains unknown fault codes {bad}")
        return self


def load_config(path: str | Path) -> SimConfig:
    with open(path, encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh)
    return SimConfig.model_validate(raw)
