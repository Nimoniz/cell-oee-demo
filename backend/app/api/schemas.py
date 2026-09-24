"""API response shapes. Timestamps are ``str``, formatted explicitly as ISO 8601 with a
trailing ``Z`` (see ``serialize.iso_z``): simulated plant time, UTC, never converted.

Ratios and any quantity that can be legitimately undefined are ``float | None``: the frontend's
single formatting path renders ``None`` as "—" and a real ``0`` as "0 %", never conflating them.
"""

from __future__ import annotations

from pydantic import BaseModel


class OeeOut(BaseModel):
    label: str
    window_start: str
    window_end: str
    gap_s: float
    opening_s: float
    planned_stop_s: float
    planned_s: float
    fault_s: float
    setup_s: float
    starved_s: float
    blocked_s: float
    manual_s: float
    micro_s: float
    operating_s: float
    good: int
    scrap: int
    dropped_parts: int
    availability: float | None
    performance: float | None
    quality: float | None
    oee: float | None
    anomalies: tuple[str, ...]


class StopOut(BaseModel):
    id: int
    start_ts: str
    end_ts: str | None
    duration_s: float  # as of "now" for an open stop
    open: bool
    state: int
    origin_station: str
    fault_code: int
    suggested_category: str | None
    qualified_cause: str | None
    qualified_at: str | None


class QualifyIn(BaseModel):
    cause: str


class ParetoBucketOut(BaseModel):
    key: str
    duration_s: float
    count: int
    unqualified_duration_s: float
    unqualified_count: int


class ParetoOut(BaseModel):
    by: str
    metric: str
    buckets: list[ParetoBucketOut]
    micro_count: int
    micro_duration_s: float


class WeldBucketOut(BaseModel):
    start: str
    end: str
    avg_ka: float | None
    min_ka: float | None
    max_ka: float | None
    points: int
    points_nok: int
    scrap_rate: float | None


class WeldMarkerOut(BaseModel):
    ts: str
    kind: str  # "dressing" | "cap_change"


class WeldOut(BaseModel):
    bucket_s: float
    nominal_ka: float
    tolerance_pct: float
    buckets: list[WeldBucketOut]
    markers: list[WeldMarkerOut]


class TimelineSegmentOut(BaseModel):
    start: str
    end: str
    state: int


class TimelineOut(BaseModel):
    window_start: str | None  # None when nothing has been observed yet
    now: str | None
    segments: list[TimelineSegmentOut]


class LiveOut(BaseModel):
    now: str | None
    state: int | None  # Cell.Mode is not persisted; MANUAL (state 7) already implies it
    fault_code: int | None
    stop_origin: int | None
    comm_lost: bool
    comm_lost_since: str | None
    good_total: int | None
    scrap_total: int | None
    theoretical_cycle_s: float | None
    last_cycle_s: float | None
    weld_current_ka: float | None
    weld_points_since_cap_change: int | None
    open_stop: StopOut | None
    stops_rev: int
    oee: OeeOut | None  # None only when there is no current shift data at all
