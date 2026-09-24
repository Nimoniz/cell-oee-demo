"""Read queries feeding the pure OEE core. Nothing here computes OEE."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain import CAUSES
from app.oee import CounterSample, Gap, StateEvent, StopFact, StopRecord, WeldPoint

from .models import collector_state, comm_gaps, counter_samples, state_events, stops, weld_samples


@dataclass(frozen=True, slots=True)
class OeeInputs:
    stops: list[StopRecord]
    samples: list[CounterSample]
    gaps: list[Gap]


def _sample(row) -> CounterSample:
    return CounterSample(row.ts, row.good_total, row.scrap_total, row.theoretical_cycle_s)


async def data_bounds(conn: AsyncConnection) -> tuple[datetime | None, datetime | None]:
    """(first, latest) simulated instants known: nothing can be said outside of them.

    The latest is the UI's "now" (latest simulated time received). The first is the earliest
    cell state event: before it the collector had seen nothing.
    """
    earliest = (
        await conn.execute(
            select(func.min(state_events.c.ts)).where(state_events.c.scope == "CELL")
        )
    ).scalar_one()
    latest = (await conn.execute(select(collector_state.c.last_seen_ts))).scalar_one_or_none()
    return earliest, latest


async def fetch_oee_inputs(conn: AsyncConnection, frm: datetime, to: datetime) -> OeeInputs:
    """Everything needed to compute OEE over [frm, to), read as it is stored.

    * stops overlapping the range, whole (micro-stop classification needs their full duration);
    * the counter samples inside the range plus the one just before it, which is the baseline of
      the first delta;
    * gaps overlapping the range *or* the interval between that baseline and the first sample:
      a part revealed by a sample that follows a gap is not observable and must be dropped.
    """
    cols = [
        counter_samples.c.ts,
        counter_samples.c.good_total,
        counter_samples.c.scrap_total,
        counter_samples.c.theoretical_cycle_s,
    ]
    lead_in = (
        await conn.execute(
            select(*cols)
            .where(counter_samples.c.ts < frm)
            .order_by(counter_samples.c.ts.desc())
            .limit(1)
        )
    ).first()
    in_range = (
        await conn.execute(
            select(*cols)
            .where(counter_samples.c.ts >= frm, counter_samples.c.ts < to)
            .order_by(counter_samples.c.ts)
        )
    ).all()
    samples = [_sample(r) for r in ([lead_in] if lead_in else []) + list(in_range)]

    stop_rows = (
        await conn.execute(
            select(stops.c.start_ts, stops.c.end_ts, stops.c.state)
            .where(stops.c.start_ts < to, or_(stops.c.end_ts.is_(None), stops.c.end_ts > frm))
            .order_by(stops.c.start_ts)
        )
    ).all()

    horizon = lead_in.ts if lead_in else frm
    gap_rows = (
        await conn.execute(
            select(comm_gaps.c.start_ts, comm_gaps.c.end_ts)
            .where(
                comm_gaps.c.start_ts < to,
                or_(comm_gaps.c.end_ts.is_(None), comm_gaps.c.end_ts > horizon),
            )
            .order_by(comm_gaps.c.start_ts)
        )
    ).all()

    return OeeInputs(
        stops=[StopRecord(r.start_ts, r.end_ts, r.state) for r in stop_rows],
        samples=samples,
        gaps=[Gap(r.start_ts, r.end_ts) for r in gap_rows],
    )


async def fetch_cell_state_events(
    conn: AsyncConnection, frm: datetime, to: datetime
) -> list[StateEvent]:
    """CELL state changes in [frm, to), plus the one just before ``frm`` (the timeline's
    lead-in: what state was active when the window started)."""
    lead_in = (
        await conn.execute(
            select(state_events.c.ts, state_events.c.state)
            .where(state_events.c.scope == "CELL", state_events.c.ts < frm)
            .order_by(state_events.c.ts.desc())
            .limit(1)
        )
    ).first()
    in_range = (
        await conn.execute(
            select(state_events.c.ts, state_events.c.state)
            .where(
                state_events.c.scope == "CELL",
                state_events.c.ts >= frm,
                state_events.c.ts < to,
            )
            .order_by(state_events.c.ts)
        )
    ).all()
    rows = ([lead_in] if lead_in else []) + list(in_range)
    return [StateEvent(r.ts, r.state) for r in rows]


async def fetch_gaps(conn: AsyncConnection, frm: datetime, to: datetime) -> list[Gap]:
    rows = (
        await conn.execute(
            select(comm_gaps.c.start_ts, comm_gaps.c.end_ts)
            .where(
                comm_gaps.c.start_ts < to,
                or_(comm_gaps.c.end_ts.is_(None), comm_gaps.c.end_ts > frm),
            )
            .order_by(comm_gaps.c.start_ts)
        )
    ).all()
    return [Gap(r.start_ts, r.end_ts) for r in rows]


@dataclass(frozen=True, slots=True)
class ParetoInputs:
    facts: list[StopFact]
    gaps: list[Gap]


async def fetch_pareto_inputs(conn: AsyncConnection, frm: datetime, to: datetime) -> ParetoInputs:
    """Stops overlapping [frm, to), whole (the Pareto classifies on the full duration too)."""
    rows = (
        await conn.execute(
            select(
                stops.c.start_ts,
                stops.c.end_ts,
                stops.c.state,
                stops.c.origin_station,
                stops.c.fault_code,
                stops.c.qualified_cause,
            )
            .where(stops.c.start_ts < to, or_(stops.c.end_ts.is_(None), stops.c.end_ts > frm))
            .order_by(stops.c.start_ts)
        )
    ).all()
    facts = [
        StopFact(r.start_ts, r.end_ts, r.state, r.origin_station, r.fault_code, r.qualified_cause)
        for r in rows
    ]
    return ParetoInputs(facts, await fetch_gaps(conn, frm, to))


@dataclass(frozen=True, slots=True)
class WeldInputs:
    points: list[WeldPoint]
    samples: list[CounterSample]  # raw, incl. the lead-in sample; feed to counter_deltas
    gaps: list[Gap]


async def fetch_weld_inputs(conn: AsyncConnection, frm: datetime, to: datetime) -> WeldInputs:
    weld_rows = (
        await conn.execute(
            select(weld_samples.c.ts, weld_samples.c.current_ka, weld_samples.c.ok)
            .where(weld_samples.c.ts >= frm, weld_samples.c.ts < to)
            .order_by(weld_samples.c.ts)
        )
    ).all()
    counters = await fetch_oee_inputs(conn, frm, to)  # reuses the same lead-in/gap logic
    return WeldInputs(
        points=[WeldPoint(r.ts, r.current_ka, r.ok) for r in weld_rows],
        samples=counters.samples,
        gaps=counters.gaps,
    )


@dataclass(frozen=True, slots=True)
class StopRow:
    id: int
    start_ts: datetime
    end_ts: datetime | None
    state: int
    origin_station: str
    fault_code: int
    suggested_category: str | None
    qualified_cause: str | None
    qualified_at: datetime | None


_STOP_COLS = (
    stops.c.id,
    stops.c.start_ts,
    stops.c.end_ts,
    stops.c.state,
    stops.c.origin_station,
    stops.c.fault_code,
    stops.c.suggested_category,
    stops.c.qualified_cause,
    stops.c.qualified_at,
)


def _stop_row(r) -> StopRow:
    return StopRow(
        r.id,
        r.start_ts,
        r.end_ts,
        r.state,
        r.origin_station,
        r.fault_code,
        r.suggested_category,
        r.qualified_cause,
        r.qualified_at,
    )


async def fetch_qualifiable_stops(
    conn: AsyncConnection,
    now: datetime,
    min_duration_s: float = 120.0,
    unqualified_only: bool = True,
    frm: datetime | None = None,
    to: datetime | None = None,
) -> list[StopRow]:
    """Non-planned stops at least ``min_duration_s`` long, including the open one if it already
    qualifies at ``now``. Computed in Python (not SQL) so the threshold and ``now`` stay exactly
    what the OEE core would use."""
    where = [stops.c.state != 2]  # PLANNED_STOP is never shown for qualification
    if unqualified_only:
        where.append(stops.c.qualified_cause.is_(None))
    if frm is not None:
        where.append(or_(stops.c.end_ts.is_(None), stops.c.end_ts > frm))
    if to is not None:
        where.append(stops.c.start_ts < to)
    rows = (
        await conn.execute(
            select(*_STOP_COLS).where(and_(*where)).order_by(stops.c.start_ts.desc())
        )
    ).all()
    out = []
    for r in rows:
        end = r.end_ts if r.end_ts is not None else now
        if (end - r.start_ts).total_seconds() >= min_duration_s:
            out.append(_stop_row(r))
    return out


async def fetch_stop(conn: AsyncConnection, stop_id: int) -> StopRow | None:
    row = (await conn.execute(select(*_STOP_COLS).where(stops.c.id == stop_id))).first()
    return _stop_row(row) if row else None


async def qualify_stop(
    conn: AsyncConnection, stop_id: int, cause: str, qualified_at: datetime
) -> StopRow | None:
    if cause not in CAUSES:
        raise ValueError(f"unknown cause {cause!r}")
    await conn.execute(
        stops.update()
        .where(stops.c.id == stop_id)
        .values(qualified_cause=cause, qualified_at=qualified_at)
    )
    return await fetch_stop(conn, stop_id)


@dataclass(frozen=True, slots=True)
class LiveSnapshot:
    now: datetime | None
    cell: tuple[datetime, int, int, int] | None  # ts, state, fault_code, stop_origin
    stations: dict[str, tuple[datetime, int, int]]  # scope -> ts, state, fault_code
    good_total: int | None
    scrap_total: int | None
    theoretical_cycle_s: float | None
    last_cycle_s: float | None
    weld_current_ka: float | None
    weld_points_since_cap_change: int | None
    open_stop: StopRow | None
    comm_lost: bool
    comm_lost_since: datetime | None


async def fetch_live_snapshot(conn: AsyncConnection) -> LiveSnapshot:
    """Everything ``GET /api/cell/live`` needs, as of the database's own idea of "now"."""
    now = (await conn.execute(select(collector_state.c.last_seen_ts))).scalar_one_or_none()
    cell_row = (
        await conn.execute(
            select(
                state_events.c.ts,
                state_events.c.state,
                state_events.c.fault_code,
                state_events.c.stop_origin,
            )
            .where(state_events.c.scope == "CELL")
            .order_by(state_events.c.ts.desc())
            .limit(1)
        )
    ).first()
    stations: dict[str, tuple[datetime, int, int]] = {}
    for scope in ("OP10", "OP20", "OP30"):
        r = (
            await conn.execute(
                select(state_events.c.ts, state_events.c.state, state_events.c.fault_code)
                .where(state_events.c.scope == scope)
                .order_by(state_events.c.ts.desc())
                .limit(1)
            )
        ).first()
        if r:
            stations[scope] = (r.ts, r.state, r.fault_code)
    counter_row = (
        await conn.execute(
            select(
                counter_samples.c.good_total,
                counter_samples.c.scrap_total,
                counter_samples.c.theoretical_cycle_s,
                counter_samples.c.last_cycle_s,
            )
            .order_by(counter_samples.c.ts.desc())
            .limit(1)
        )
    ).first()
    weld_row = (
        await conn.execute(
            select(weld_samples.c.current_ka, weld_samples.c.points_since_cap_change)
            .order_by(weld_samples.c.ts.desc())
            .limit(1)
        )
    ).first()
    open_stop_row = (
        await conn.execute(select(*_STOP_COLS).where(stops.c.end_ts.is_(None)))
    ).first()
    open_gap_start = (
        await conn.execute(
            select(comm_gaps.c.start_ts).where(comm_gaps.c.end_ts.is_(None)).limit(1)
        )
    ).scalar_one_or_none()

    return LiveSnapshot(
        now=now,
        cell=(cell_row.ts, cell_row.state, cell_row.fault_code, cell_row.stop_origin)
        if cell_row
        else None,
        stations=stations,
        good_total=counter_row.good_total if counter_row else None,
        scrap_total=counter_row.scrap_total if counter_row else None,
        theoretical_cycle_s=counter_row.theoretical_cycle_s if counter_row else None,
        last_cycle_s=counter_row.last_cycle_s if counter_row else None,
        weld_current_ka=weld_row.current_ka if weld_row else None,
        weld_points_since_cap_change=weld_row.points_since_cap_change if weld_row else None,
        open_stop=_stop_row(open_stop_row) if open_stop_row else None,
        comm_lost=open_gap_start is not None,
        comm_lost_since=open_gap_start,
    )
