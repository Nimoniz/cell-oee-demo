"""CollectorStore against a real TimescaleDB: idempotent writes, atomic batches, resume, repair."""

from __future__ import annotations

import pytest
import pytest_asyncio
from conftest import t
from sim_helpers import raw, sim_changes, sim_config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from test_collector_units import FakeDb, as_groups, current_snapshot, feed

from app.collector.store import CollectorStore
from app.collector.tracker import StopTracker
from app.collector.types import (
    HEARTBEAT,
    ClosedGapOp,
    CloseGapOp,
    CloseStopOp,
    CounterSampleOp,
    OpenGapOp,
    OpenStopOp,
    StateEventOp,
)

pytestmark = pytest.mark.db


@pytest_asyncio.fixture
async def engine(sync_engine, migrated_db_url: str):
    eng: AsyncEngine = create_async_engine(migrated_db_url)
    yield eng
    await eng.dispose()


async def rows(engine: AsyncEngine, sql: str) -> list[tuple]:
    async with engine.connect() as conn:
        return [tuple(r) for r in (await conn.execute(text(sql))).all()]


async def dump(engine: AsyncEngine) -> dict[str, list[tuple]]:
    return {
        "events": await rows(
            engine,
            "SELECT scope, ts, state, fault_code, stop_origin FROM state_events ORDER BY scope, ts",
        ),
        "stops": await rows(
            engine,
            "SELECT start_ts, end_ts, state, origin_station, fault_code, suggested_category FROM stops ORDER BY start_ts",
        ),
        "counters": await rows(
            engine,
            "SELECT ts, good_total, scrap_total, theoretical_cycle_s, last_cycle_s FROM counter_samples ORDER BY ts",
        ),
        "welds": await rows(
            engine,
            "SELECT ts, current_ka, points_since_cap_change, ok FROM weld_samples ORDER BY ts",
        ),
        "gaps": await rows(engine, "SELECT start_ts, end_ts FROM comm_gaps ORDER BY start_ts"),
    }


def batches(groups, size=25):
    """Ops as the collector would produce them, a few groups per transaction."""
    tracker = StopTracker()
    out, current = [], []
    for i, group in enumerate(groups, 1):
        current += tracker.apply(group)
        if i % size == 0:
            out.append(current)
            current = []
    out.append(current)
    return out


async def test_the_database_holds_exactly_what_the_in_memory_model_predicts(engine) -> None:
    changes = [c for c in raw(sim_changes(sim_config(), hours=8)) if c.tag != HEARTBEAT]
    store = CollectorStore(engine)
    fake = FakeDb()
    for ops in batches(as_groups(changes)):
        await store.apply(ops)
        fake.apply(ops)

    got = await dump(engine)
    assert len(got["stops"]) > 5 and len(got["counters"]) > 300 and len(got["welds"]) > 3000
    assert got["events"] == [
        (scope, ts, *vals) for (scope, ts), vals in sorted(fake.events.items())
    ]
    assert [(s[0], s[1]) for s in got["stops"]] == [
        (start, st["end"]) for start, st in sorted(fake.stops.items())
    ]
    assert [(c[0], c[1], c[2]) for c in got["counters"]] == [
        (ts, v[0], v[1]) for ts, v in sorted(fake.counters.items())
    ]
    assert len(got["welds"]) == len(fake.welds)


async def test_applying_the_same_batches_twice_stores_them_once(engine) -> None:
    changes = [c for c in raw(sim_changes(sim_config(), hours=4)) if c.tag != HEARTBEAT]
    store = CollectorStore(engine)
    all_batches = batches(as_groups(changes))
    for ops in all_batches:
        await store.apply(ops)
    first = await dump(engine)
    for ops in all_batches:  # the whole history delivered again
        await store.apply(ops)
    assert await dump(engine) == first


async def test_a_closed_stop_gets_its_duration(engine) -> None:
    store = CollectorStore(engine)
    await store.apply(
        [
            StateEventOp("CELL", t(6), 3, 202, 20),
            OpenStopOp(t(6), 3, "OP20", 202, "electrical_breakdown"),
        ]
    )
    (open_stop,) = await rows(engine, "SELECT end_ts, duration_s, is_induced FROM stops")
    assert open_stop == (None, None, False)
    await store.apply([CloseStopOp(t(6), t(6, 12, 30.5))])
    (closed,) = await rows(engine, "SELECT end_ts, duration_s FROM stops")
    assert closed == (t(6, 12, 30.5), 750.5)
    await store.apply([CloseStopOp(t(6), t(7))])  # closing twice changes nothing
    assert (await rows(engine, "SELECT end_ts FROM stops"))[0][0] == t(6, 12, 30.5)


async def test_a_batch_is_atomic(engine) -> None:
    store = CollectorStore(engine)
    with pytest.raises((IntegrityError, DBAPIError)):
        await store.apply(
            [
                StateEventOp("CELL", t(6), 3, 202, 20),
                OpenStopOp(t(6), 3, "OP20", 202, None),
                OpenStopOp(t(7), 4, "OP20", 0, None),  # a second open stop: rejected
            ],
            last_seen=t(7),
        )
    assert await rows(engine, "SELECT count(*) FROM state_events") == [(0,)]
    assert await rows(engine, "SELECT count(*) FROM stops") == [(0,)]
    assert await rows(engine, "SELECT count(*) FROM collector_state") == [(0,)]  # nothing leaked


async def test_last_seen_only_moves_forward(engine) -> None:
    store = CollectorStore(engine)
    await store.apply([], last_seen=t(6))
    await store.apply([], last_seen=t(5))  # an older value (e.g. a replayed snapshot)
    assert (await rows(engine, "SELECT last_seen_ts FROM collector_state"))[0][0] == t(6)
    await store.apply([], last_seen=t(7))
    assert (await rows(engine, "SELECT last_seen_ts FROM collector_state"))[0][0] == t(7)


async def test_gaps_open_close_and_can_be_recorded_closed(engine) -> None:
    store = CollectorStore(engine)
    await store.apply([OpenGapOp(t(6)), ClosedGapOp(t(5), t(5, 2))])
    await store.apply([OpenGapOp(t(6))])  # replay
    assert await rows(engine, "SELECT start_ts, end_ts FROM comm_gaps ORDER BY start_ts") == [
        (t(5), t(5, 2)),
        (t(6), None),
    ]
    await store.apply([CloseGapOp(t(6), t(6, 4))])
    resume = await store.resume()
    assert resume.open_gap_start is None


async def test_resume_then_replaying_the_subscription_changes_nothing(engine) -> None:
    changes = [c for c in raw(sim_changes(sim_config(), hours=6)) if c.tag != HEARTBEAT]
    store = CollectorStore(engine)
    groups = as_groups(changes)
    cut = len(groups) // 2
    for ops in batches(groups[:cut]):
        await store.apply(ops)
    before = await dump(engine)

    resume = await store.resume()  # the collector restarts
    tracker = StopTracker(resume.tracker)
    head = [c for g in groups[:cut] for c in _flat(g)]
    replay_ops = []
    for group in as_groups(current_snapshot(head)):
        replay_ops += tracker.apply(group)
    assert replay_ops == []  # no duplicate event, no phantom stop
    await store.apply(replay_ops)
    assert await dump(engine) == before

    tail_ops = []
    for group in groups[cut:]:
        tail_ops += tracker.apply(group)
    await store.apply(tail_ops)
    uninterrupted = FakeDb()
    feed(StopTracker(), uninterrupted, changes)
    assert len((await dump(engine))["stops"]) == len(uninterrupted.stops)


def _flat(group):
    from app.collector.types import RawChange

    return [RawChange(tag, value, group.ts) for tag, value in group.values.items()]


# ------------------------------------------------------------------ repair of the open stop


async def seed_event(engine, state, ts, fault=0, origin=0) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO state_events (scope, ts, state, fault_code, stop_origin) "
                "VALUES ('CELL', :ts, :s, :f, :o)"
            ),
            {"ts": ts, "s": state, "f": fault, "o": origin},
        )


async def test_resume_repairs_a_stop_left_open_after_the_cell_went_back_to_producing(
    engine,
) -> None:
    await seed_event(engine, 3, t(6), 202, 20)
    await seed_event(engine, 1, t(6, 20))
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO stops (start_ts, state, origin_station, fault_code) VALUES (:t, 3, 'OP20', 202)"
            ),
            {"t": t(6)},
        )
    resume = await CollectorStore(engine).resume()
    assert resume.tracker.open_stop_start is None
    assert await rows(engine, "SELECT end_ts, duration_s FROM stops") == [(t(6, 20), 1200.0)]


async def test_resume_reopens_the_stop_of_the_latest_non_producing_event(engine) -> None:
    await seed_event(engine, 5, t(6), 0, 10)  # STARVED since 06:00, but no stop row
    resume = await CollectorStore(engine).resume()
    assert resume.tracker.open_stop_start == t(6)
    assert await rows(
        engine, "SELECT start_ts, end_ts, state, origin_station, suggested_category FROM stops"
    ) == [(t(6), None, 5, "OP10", "missing_parts")]


async def test_resume_of_a_consistent_database_touches_nothing(engine) -> None:
    store = CollectorStore(engine)
    await store.apply(
        [
            StateEventOp("CELL", t(6), 3, 101, 10),
            OpenStopOp(t(6), 3, "OP10", 101, "missing_parts"),
            CounterSampleOp(t(6), 5, 0, 55.0, 56.0),
        ],
        last_seen=t(6, 1),
    )
    before = await dump(engine)
    resume = await store.resume()
    assert await dump(engine) == before
    assert resume.tracker.open_stop_start == t(6)
    assert resume.tracker.last_cell == (t(6), 3, 101, 10)
    assert resume.tracker.last_counter_ts == t(6)
    assert resume.last_seen_ts == t(6, 1)
