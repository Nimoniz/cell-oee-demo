"""Glue between the database and the pure OEE core (used by the API)."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import OeeCfg
from app.db.repo import data_bounds, fetch_cell_state_events, fetch_oee_inputs
from app.oee import (
    GroupBy,
    OeeResult,
    TimelineSegment,
    Window,
    build_timeline,
    compute_oee_grouped,
    compute_oee_windows,
)

_MAX_SHIFT_LOOKBACK = timedelta(hours=9)  # > the longest shift (8 h): enough to find its start


async def oee_over_range(
    conn: AsyncConnection,
    cfg: OeeCfg,
    frm: datetime,
    to: datetime,
    group_by: GroupBy | None = None,
) -> list[OeeResult]:
    """OEE of [frm, to): one result per shift / production day, or one for the whole range.

    Computed at read time from stored stops, counter samples and gaps. The range is clipped to
    what was actually observed: nothing before the first recorded event, nothing after the latest
    simulated time received (the UI's "now"), which also bounds open stops. A period with no
    observed time yields ``None`` ratios, never a misleading 0 or 100 %.
    """
    earliest, latest = await data_bounds(conn)
    if earliest is None or latest is None:
        frm = to = max(frm, to)  # nothing recorded yet: an empty window
    else:
        frm, to = max(frm, earliest), min(to, latest)
        to = max(to, frm)
    inputs = await fetch_oee_inputs(conn, frm, to)
    until = latest or to
    if group_by is None:
        return compute_oee_windows(
            [Window("range", frm, to)], inputs.stops, inputs.samples, inputs.gaps, cfg.params, until
        )
    return compute_oee_grouped(
        frm, to, group_by, cfg.plan, inputs.stops, inputs.samples, inputs.gaps, cfg.params, until
    )


def current_shift_start(now: datetime, cfg: OeeCfg) -> datetime:
    """Start of the shift ``now`` falls into."""
    from app.oee import shift_windows  # local import: avoids a cycle with app.oee.windows

    windows = shift_windows(now - _MAX_SHIFT_LOOKBACK, now + timedelta(seconds=1), cfg.plan)
    return windows[-1].start


async def live_oee(conn: AsyncConnection, cfg: OeeCfg, now: datetime) -> OeeResult:
    """OEE of the current shift, from its start to ``now`` — trusting ``now`` as given.

    Unlike ``oee_over_range``, this never re-reads or clips against ``data_bounds``: the caller
    (the live WebSocket hub) already knows "now" from the stream of notifications, and it must
    stay the sole source of truth for what "live" means, never falling back to the database's own
    ``last_seen_ts``.
    """
    start = current_shift_start(now, cfg)
    inputs = await fetch_oee_inputs(conn, start, now)
    window = Window("current_shift", start, now)
    (result,) = compute_oee_windows(
        [window], inputs.stops, inputs.samples, inputs.gaps, cfg.params, now
    )
    return result


async def shift_timeline(
    conn: AsyncConnection, cfg: OeeCfg, now: datetime
) -> list[TimelineSegment]:
    """State history of the current shift, from its start to ``now`` — for the live andon bar.

    Same "trust ``now`` as given" rule as ``live_oee``: no re-clipping against ``data_bounds``.
    """
    start = current_shift_start(now, cfg)
    events = await fetch_cell_state_events(conn, start, now)
    return build_timeline(events, Window("current_shift", start, now), now)
