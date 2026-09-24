"""Internal dataclasses -> API schemas."""

from __future__ import annotations

from datetime import UTC, datetime

from app.db.repo import LiveSnapshot, StopRow
from app.oee import OeeResult, ParetoResult, TimelineSegment, WeldBucket

from .schemas import (
    LiveOut,
    OeeOut,
    ParetoBucketOut,
    ParetoOut,
    StopOut,
    TimelineOut,
    TimelineSegmentOut,
    WeldBucketOut,
    WeldOut,
)


def iso_z(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def oee_out(r: OeeResult, live_min_required_s: float | None = None) -> OeeOut:
    """``live_min_required_s`` blanks the ratios (not the raw times) below that much required
    time: a live view a few seconds into a shift would otherwise show a misleadingly low %."""
    availability, performance, quality, oee = r.availability, r.performance, r.quality, r.oee
    if live_min_required_s is not None and r.planned_s < live_min_required_s:
        availability = performance = quality = oee = None
    return OeeOut(
        label=r.window.label,
        window_start=iso_z(r.window.start),
        window_end=iso_z(r.window.end),
        gap_s=r.gap_s,
        opening_s=r.opening_s,
        planned_stop_s=r.planned_stop_s,
        planned_s=r.planned_s,
        fault_s=r.fault_s,
        setup_s=r.setup_s,
        starved_s=r.starved_s,
        blocked_s=r.blocked_s,
        manual_s=r.manual_s,
        micro_s=r.micro_s,
        operating_s=r.operating_s,
        good=r.good,
        scrap=r.scrap,
        dropped_parts=r.dropped_parts,
        availability=availability,
        performance=performance,
        quality=quality,
        oee=oee,
        anomalies=r.anomalies,
    )


def stop_out(row: StopRow, now: datetime) -> StopOut:
    end = row.end_ts
    duration = ((end or now) - row.start_ts).total_seconds()
    return StopOut(
        id=row.id,
        start_ts=iso_z(row.start_ts),
        end_ts=iso_z(end) if end else None,
        duration_s=duration,
        open=end is None,
        state=row.state,
        origin_station=row.origin_station,
        fault_code=row.fault_code,
        suggested_category=row.suggested_category,
        qualified_cause=row.qualified_cause,
        qualified_at=iso_z(row.qualified_at) if row.qualified_at else None,
    )


def pareto_out(r: ParetoResult) -> ParetoOut:
    return ParetoOut(
        by=r.by,
        metric=r.metric,
        buckets=[
            ParetoBucketOut(
                key=b.key,
                duration_s=b.duration_s,
                count=b.count,
                unqualified_duration_s=b.unqualified_duration_s,
                unqualified_count=b.unqualified_count,
            )
            for b in r.buckets
        ],
        micro_count=r.micro_count,
        micro_duration_s=r.micro_duration_s,
    )


def weld_bucket_out(b: WeldBucket) -> WeldBucketOut:
    return WeldBucketOut(
        start=iso_z(b.start),
        end=iso_z(b.end),
        avg_ka=b.avg_ka,
        min_ka=b.min_ka,
        max_ka=b.max_ka,
        points=b.points,
        points_nok=b.points_nok,
        scrap_rate=b.scrap_rate,
    )


def weld_out(
    buckets: list[WeldBucket], markers, bucket_s: float, nominal_ka: float, tolerance_pct: float
) -> WeldOut:
    return WeldOut(
        bucket_s=bucket_s,
        nominal_ka=nominal_ka,
        tolerance_pct=tolerance_pct,
        buckets=[weld_bucket_out(b) for b in buckets],
        markers=markers,
    )


def timeline_out(
    segments: list[TimelineSegment], window_start: datetime | None, now: datetime | None
) -> TimelineOut:
    return TimelineOut(
        window_start=iso_z(window_start) if window_start else None,
        now=iso_z(now) if now else None,
        segments=[
            TimelineSegmentOut(start=iso_z(s.start), end=iso_z(s.end), state=s.state)
            for s in segments
        ],
    )


def live_out(
    snapshot: LiveSnapshot,
    oee: OeeResult | None,
    stops_rev: int,
    now: datetime | None,
    live_min_required_s: float,
) -> LiveOut:
    cell = snapshot.cell
    return LiveOut(
        now=iso_z(now) if now else None,
        state=cell[1] if cell else None,
        fault_code=cell[2] if cell else None,
        stop_origin=cell[3] if cell else None,
        comm_lost=snapshot.comm_lost,
        comm_lost_since=iso_z(snapshot.comm_lost_since) if snapshot.comm_lost_since else None,
        good_total=snapshot.good_total,
        scrap_total=snapshot.scrap_total,
        theoretical_cycle_s=snapshot.theoretical_cycle_s,
        last_cycle_s=snapshot.last_cycle_s,
        weld_current_ka=snapshot.weld_current_ka,
        weld_points_since_cap_change=snapshot.weld_points_since_cap_change,
        open_stop=stop_out(snapshot.open_stop, now or datetime.now(UTC))
        if snapshot.open_stop
        else None,
        stops_rev=stops_rev,
        oee=oee_out(oee, live_min_required_s) if oee else None,
    )
