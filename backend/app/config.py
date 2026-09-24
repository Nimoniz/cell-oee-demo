"""Backend settings: ``config.yaml`` plus the ``DATABASE_URL`` environment variable."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .oee.types import OeeParams, ShiftDef, ShiftPlan

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
DEFAULT_DATABASE_URL = "postgresql+psycopg://oee:oee@localhost:5432/oee"

_HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def _seconds(hhmm: str) -> int:
    if not _HHMM.fullmatch(hhmm):
        raise ValueError(f"expected 'HH:MM', got {hhmm!r}")
    h, m = hhmm.split(":")
    return int(h) * 3600 + int(m) * 60


class ShiftCfg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _valid(cls, v: str) -> str:
        _seconds(v)
        return v


class OeeCfg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    micro_stop_s: float = Field(default=120.0, ge=0)
    rollover_window: int = Field(default=1000, gt=0)
    day_start: str = "05:00"
    shifts: tuple[ShiftCfg, ...]

    @model_validator(mode="after")
    def _contiguous(self) -> OeeCfg:
        _seconds(self.day_start)
        if not self.shifts:
            raise ValueError("at least one shift is required")
        pairs = zip(self.shifts, self.shifts[1:] + self.shifts[:1], strict=True)
        for cur, nxt in pairs:
            if cur.end != nxt.start:
                raise ValueError(f"shift {cur.name!r} must end when {nxt.name!r} starts")
        return self

    @property
    def params(self) -> OeeParams:
        return OeeParams(micro_stop_s=self.micro_stop_s, rollover_window=self.rollover_window)

    @property
    def plan(self) -> ShiftPlan:
        return ShiftPlan(
            shifts=tuple(ShiftDef(s.name, _seconds(s.start), _seconds(s.end)) for s in self.shifts),
            day_start_s=_seconds(self.day_start),
        )


class CollectorCfg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    opcua_endpoint: str = "opc.tcp://simulator:4840"
    namespace_uri: str = "urn:cell-oee-demo:plc"
    # Subscription tuned for up to 60x: the shortest events (a 30 s tip dressing, a weld point
    # every 4.6 s) last 0.5 s and 76 ms of wall-clock time. Queues are FIFO and never coalesce.
    publish_interval_ms: float = Field(default=250.0, gt=0)
    sampling_interval_ms: float = Field(default=50.0, gt=0)
    queue_size_fast: int = Field(default=1000, gt=0)  # heartbeat, weld points
    queue_size_events: int = Field(default=200, gt=0)  # states, counters
    # Heartbeat watchdog: timeout = max(wall_floor, sim_seconds / ACCELERATION), in wall time.
    heartbeat_wall_floor_s: float = Field(default=3.0, gt=0)
    heartbeat_sim_s: float = Field(default=5.0, gt=0)
    reconnect_min_s: float = Field(default=0.5, gt=0)
    reconnect_max_s: float = Field(default=10.0, gt=0)
    last_seen_flush_s: float = Field(default=1.0, gt=0)
    # A change is held this long (wall clock) before being processed, so that all the tags
    # written at the same SourceTimestamp have arrived: > publish + sampling interval.
    assembler_settle_s: float = Field(default=0.6, gt=0)
    health_file: str = "/tmp/collector.health"  # noqa: S108


class ApiCfg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    cors_origins: tuple[str, ...] = ("*",)
    # Live WebSocket: a full snapshot is pushed at most this often, whatever the notification
    # rate or ACCELERATION is — every message is a full snapshot, so this also acts as the
    # keepalive (a client with no message for a few intervals knows the feed is dead).
    ws_publish_interval_ms: float = Field(default=250.0, gt=0)
    # Live OEE is recomputed at most this often (wall clock), once, shared by every client.
    oee_refresh_interval_s: float = Field(default=1.0, gt=0)
    # Below this much required time into the current shift, the live OEE is "—", not a
    # misleadingly low percentage.
    live_min_required_s: float = Field(default=300.0, ge=0)
    # Duplicated from simulator/config.yaml (weld section), to draw the tolerance band.
    weld_nominal_ka: float = Field(default=8.0, gt=0)
    weld_tolerance_pct: float = Field(default=10.0, gt=0)
    weld_target_buckets: int = Field(default=300, gt=0)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    oee: OeeCfg
    collector: CollectorCfg = CollectorCfg()
    api: ApiCfg = ApiCfg()
    database_url: str = DEFAULT_DATABASE_URL
    # Simulation speed factor (1 on a real PLC). Read from the ACCELERATION environment variable.
    acceleration: float = Field(default=1.0, ge=1.0, le=60.0)
    # LISTEN/NOTIFY channel the collector wakes the API up on after each committed write.
    notify_channel: str = "cell_events"

    @property
    def heartbeat_timeout_s(self) -> float:
        """Wall-clock silence after which communication is declared lost.

        max(3 s wall, 5 s simulated / ACCELERATION): a real loss stops simulated time, so the
        floor is in real time; at 60x, 5 simulated seconds are only 83 ms, below any jitter.
        """
        c = self.collector
        return max(c.heartbeat_wall_floor_s, c.heartbeat_sim_s / self.acceleration)


def load_settings(path: str | Path = DEFAULT_CONFIG_PATH) -> Settings:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    raw["database_url"] = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    raw["acceleration"] = float(os.environ.get("ACCELERATION", "1"))
    collector = raw.setdefault("collector", {})
    if "OPCUA_ENDPOINT" in os.environ:
        collector["opcua_endpoint"] = os.environ["OPCUA_ENDPOINT"]
    if "CORS_ORIGINS" in os.environ:
        raw.setdefault("api", {})["cors_origins"] = tuple(
            o.strip() for o in os.environ["CORS_ORIGINS"].split(",") if o.strip()
        )
    return Settings.model_validate(raw)
