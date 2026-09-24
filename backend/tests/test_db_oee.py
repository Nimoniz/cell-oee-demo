"""OEE computed at read time from what is stored in TimescaleDB."""

from __future__ import annotations

import random
from datetime import timedelta

import pytest
import pytest_asyncio
from conftest import PLAN, t
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import OeeCfg, ShiftCfg
from app.db.repo import data_bounds, fetch_oee_inputs
from app.oee import (
    CounterSample,
    Gap,
    OeeParams,
    StopRecord,
    check_invariant,
    combine,
    compute_oee_grouped,
)
from app.service import oee_over_range

pytestmark = pytest.mark.db

CFG = OeeCfg(
    shifts=(
        ShiftCfg(name="matin", start="05:00", end="13:00"),
        ShiftCfg(name="apres-midi", start="13:00", end="21:00"),
        ShiftCfg(name="nuit", start="21:00", end="05:00"),
    )
)


@pytest_asyncio.fixture
async def engine(sync_engine, migrated_db_url: str):
    """Async engine on the (truncated) migrated database."""
    eng = create_async_engine(migrated_db_url)
    yield eng
    await eng.dispose()


def load(sync_engine, stops=(), samples=(), gaps=(), first_event=None, last_seen=None) -> None:
    """Write facts the way the collector will: stops, raw samples, gaps, and its own progress."""
    with sync_engine.begin() as conn:
        for s in stops:
            duration = (s.end - s.start).total_seconds() if s.end else None
            conn.execute(
                text(
                    "INSERT INTO stops (start_ts, end_ts, duration_s, state, origin_station) "
                    "VALUES (:a, :b, :d, :st, 'OP20')"
                ),
                {"a": s.start, "b": s.end, "d": duration, "st": s.state},
            )
        for c in samples:
            conn.execute(
                text("INSERT INTO counter_samples VALUES (:t, :g, :s, :tct, :tct)"),
                {"t": c.ts, "g": c.good_total, "s": c.scrap_total, "tct": c.theoretical_cycle_s},
            )
        for g in gaps:
            conn.execute(
                text("INSERT INTO comm_gaps (start_ts, end_ts) VALUES (:a, :b)"),
                {"a": g.start, "b": g.end},
            )
        if first_event is not None:
            conn.execute(
                text(
                    "INSERT INTO state_events (scope, ts, state, stop_origin) VALUES ('CELL', :t, 1, 0)"
                ),
                {"t": first_event},
            )
        if last_seen is not None:
            conn.execute(
                text("INSERT INTO collector_state (last_seen_ts) VALUES (:t)"), {"t": last_seen}
            )


def synthetic_day(seed: int) -> tuple[list[StopRecord], list[CounterSample], list[Gap]]:
    """Two production days of plausible facts, including stops across shift boundaries."""
    rng = random.Random(seed)
    stops: list[StopRecord] = []
    cursor = t(5)
    end = t(5, day=2)
    while cursor < end:
        cursor += timedelta(minutes=rng.randint(20, 240))
        length = timedelta(seconds=rng.choice([30, 90, 119, 120, 121, 300, 900, 2400]))
        stops.append(StopRecord(cursor, cursor + length, rng.choice([2, 3, 3, 3, 4, 5, 6, 7])))
        cursor += length
    for shift_start in (t(9), t(17), t(1, day=1), t(9, day=1), t(17, day=1)):  # breaks
        stops = [
            s
            for s in stops
            if not (s.start < shift_start + timedelta(minutes=20) and s.end > shift_start)
        ]
        stops.append(StopRecord(shift_start, shift_start + timedelta(minutes=20), 2))
    stops.sort(key=lambda s: s.start)

    samples, good, scrap, ts = [], 2**32 - 30, 0, t(5)
    while ts < end:
        ts += timedelta(seconds=rng.uniform(50, 62))
        if rng.random() < 0.02:
            scrap += 1
        else:
            good = (good + 1) % 2**32
        if rng.random() < 0.002:  # a PLC restart
            good = scrap = 0
        samples.append(CounterSample(ts, good, scrap, 55.0))
    gaps = [Gap(t(10, 5), t(10, 25)), Gap(t(23, 0), t(23, 0, 45))]
    # Keep facts consistent: drop a stop that lies entirely in a gap.
    return stops, samples, gaps


# ------------------------------------------------------------------ inputs


async def test_inputs_include_the_baseline_sample_and_overlapping_rows(sync_engine, engine) -> None:
    load(
        sync_engine,
        stops=[
            StopRecord(t(4), t(4, 30), 3),  # before the range
            StopRecord(t(4, 50), t(5, 10), 3),  # straddles the start
            StopRecord(t(6), t(6, 10), 4),
            StopRecord(t(6, 55), t(7, 5), 5),  # straddles the end
            StopRecord(t(8), t(8, 10), 3),  # after
        ],
        samples=[
            CounterSample(t(3), 1, 0, 55),
            CounterSample(t(4, 59), 5, 0, 55),  # the baseline: last one before the range
            CounterSample(t(5, 1), 6, 0, 55),
            CounterSample(t(6, 59), 9, 0, 55),
            CounterSample(t(7, 1), 10, 0, 55),  # after the range
        ],
        gaps=[
            Gap(t(2), t(2, 30)),
            Gap(t(4, 20), t(4, 30)),  # before the baseline sample: irrelevant
            Gap(t(4, 59, 10), t(4, 59, 50)),  # between the baseline and the range start
            Gap(t(6, 30), None),
            Gap(t(9), t(9, 5)),
        ],
    )
    async with engine.connect() as conn:
        inputs = await fetch_oee_inputs(conn, t(5), t(7))
    assert [(s.start, s.state) for s in inputs.stops] == [(t(4, 50), 3), (t(6), 4), (t(6, 55), 5)]
    assert [c.ts for c in inputs.samples] == [t(4, 59), t(5, 1), t(6, 59)]
    # A gap between the baseline and the range start matters: the first delta spans it.
    assert [g.start for g in inputs.gaps] == [t(4, 59, 10), t(6, 30)]


async def test_data_bounds(sync_engine, engine) -> None:
    async with engine.connect() as conn:
        assert await data_bounds(conn) == (None, None)
    load(sync_engine, first_event=t(5), last_seen=t(8))
    async with engine.connect() as conn:
        assert await data_bounds(conn) == (t(5), t(8))


# ------------------------------------------------------------------ OEE from the database


async def test_oee_from_the_database_equals_the_pure_computation(sync_engine, engine) -> None:
    stops, samples, gaps = synthetic_day(seed=7)
    load(sync_engine, stops, samples, gaps, first_event=t(5), last_seen=t(5, day=2))
    frm, to = t(5), t(5, day=2)

    async with engine.connect() as conn:
        by_shift = await oee_over_range(conn, CFG, frm, to, "shift")
        by_day = await oee_over_range(conn, CFG, frm, to, "day")
        whole = await oee_over_range(conn, CFG, frm, to)

    expected = compute_oee_grouped(frm, to, "shift", PLAN, stops, samples, gaps, OeeParams(), to)
    assert len(by_shift) == 6
    for got, want in zip(by_shift, expected, strict=True):
        assert got.window == want.window
        assert got.oee == pytest.approx(want.oee)
        assert got.planned_s == pytest.approx(want.planned_s)
        assert (got.good, got.scrap, got.dropped_parts) == (
            want.good,
            want.scrap,
            want.dropped_parts,
        )

    for result in (*by_shift, *by_day, *whole):
        check_invariant(result)  # the reference invariant, on data that went through the database
    assert combine(by_shift).oee == pytest.approx(whole[0].oee)
    assert combine(by_day).oee == pytest.approx(whole[0].oee)
    assert combine(by_shift).gap_s == 20 * 60 + 45  # both gaps, excluded from the required time


async def test_the_micro_stop_threshold_is_applied_at_read_time(sync_engine, engine) -> None:
    load(
        sync_engine,
        stops=[StopRecord(t(6), t(6, 3), 3)],  # 180 s
        first_event=t(5),
        last_seen=t(8),
    )
    strict = OeeCfg(**{**CFG.model_dump(), "micro_stop_s": 120})
    lenient = OeeCfg(**{**CFG.model_dump(), "micro_stop_s": 300})
    async with engine.connect() as conn:
        (a,) = await oee_over_range(conn, strict, t(5), t(8))
        (b,) = await oee_over_range(conn, lenient, t(5), t(8))
    assert (a.fault_s, a.micro_s) == (180, 0)
    assert (b.fault_s, b.micro_s) == (0, 180)  # same stored data, another threshold, no migration


async def test_range_is_clipped_to_what_was_observed(sync_engine, engine) -> None:
    load(sync_engine, first_event=t(6), last_seen=t(8))
    async with engine.connect() as conn:
        (r,) = await oee_over_range(conn, CFG, t(0), t(23))
        before = await oee_over_range(conn, CFG, t(1), t(5))
        after = await oee_over_range(conn, CFG, t(9), t(12))
        by_shift = await oee_over_range(conn, CFG, t(0), t(23), "shift")
    assert (r.window.start, r.window.end) == (t(6), t(8))  # not the 23 h asked for
    assert before[0].oee is None and after[0].oee is None  # nothing observed: null, not 0
    assert [w.window.start for w in by_shift] == [t(6)]


async def test_an_empty_database_gives_null_not_an_error(engine) -> None:
    async with engine.connect() as conn:
        (r,) = await oee_over_range(conn, CFG, t(5), t(13))
        by_day = await oee_over_range(conn, CFG, t(5), t(13), "day")
    assert r.oee is None and r.availability is None
    assert by_day == []


async def test_an_open_stop_lasts_until_the_latest_simulated_time(sync_engine, engine) -> None:
    load(sync_engine, stops=[StopRecord(t(6), None, 3)], first_event=t(5), last_seen=t(6, 1, 30))
    async with engine.connect() as conn:
        (young,) = await oee_over_range(conn, CFG, t(5), t(7))
        await conn.rollback()
    with sync_engine.begin() as c:
        c.execute(text("UPDATE collector_state SET last_seen_ts = :t"), {"t": t(6, 2, 30)})
    async with engine.connect() as conn:
        (old,) = await oee_over_range(conn, CFG, t(5), t(7))
    assert (young.fault_s, young.micro_s) == (0, 90)  # still a micro-stop
    assert (old.fault_s, old.micro_s) == (150, 0)  # crossed 120 s: a loss, since its start


async def test_parts_across_a_stored_gap_are_dropped(sync_engine, engine) -> None:
    load(
        sync_engine,
        samples=[
            CounterSample(t(5), 0, 0, 55),
            CounterSample(t(5, 10), 10, 0, 55),
            CounterSample(t(5, 50), 30, 0, 55),
            CounterSample(t(6), 40, 0, 55),
        ],
        gaps=[Gap(t(5, 20), t(5, 40))],
        first_event=t(5),
        last_seen=t(7),
    )
    async with engine.connect() as conn:
        (r,) = await oee_over_range(conn, CFG, t(5), t(7))
    assert (r.good, r.dropped_parts, r.gap_s) == (20, 20, 1_200)
    check_invariant(r)
