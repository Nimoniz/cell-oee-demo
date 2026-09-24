"""End to end: real simulator -> real OPC UA -> collector -> TimescaleDB, with chaos.

Everything runs in one event loop at 60x (one wall-clock second = one simulated minute). The
simulator has its chaos section on:

* counters start 12 (good) and 2 (scrap) counts below 2**32: a UInt32 **rollover** within minutes;
* the counters are **reset** to 0 every simulated hour (a PLC restart without retentive data);
* the **heartbeat freezes** for 240 simulated seconds (4 s of wall-clock time: longer than the
  3 s watchdog) every 0.8 simulated hour, while the rest of the PLC keeps producing.

The collector is killed without warning once and stopped cleanly once, and restarted each time.

The simulation engine is deterministic, so the run is checked against an offline replay of the
very same simulation (same config, same seed, same chaos): what the database holds must be
exactly what the PLC did, except where the collector was not looking.
"""

from __future__ import annotations

import asyncio
import socket
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sim_helpers import Simulation, as_groups_ts, dt, raw, serve, sim_config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.collector.pipeline import Collector
from app.collector.types import CELL_FAULT, CELL_ORIGIN, CELL_STATE, GOOD, HEARTBEAT, SCRAP
from app.config import load_settings
from app.db import migrate
from app.db.repo import data_bounds
from app.domain import CellState
from app.oee import CounterSample, DeltaKind, check_invariant, combine, counter_deltas
from app.service import oee_over_range

pytestmark = [pytest.mark.e2e, pytest.mark.db]

START = "2026-01-05T08:00:00"
FREEZE_S = 240


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Rig:
    """The simulator and successive collector instances, on one event loop."""

    def __init__(self, engine: AsyncEngine, db_url: str, tmp: Path) -> None:
        self.engine = engine
        port = free_port()
        self.sim_cfg = sim_config(
            clock={"start": START, "acceleration": 60},
            opcua={"endpoint": f"opc.tcp://127.0.0.1:{port}"},
            chaos={
                "enabled": True,
                "counters_at_start": {"good": 2**32 - 12, "scrap": 2**32 - 2},
                "counter_reset_every_h": 1,
                "heartbeat_freeze": {"every_h": 0.8, "duration_s": FREEZE_S},
            },
        )
        base = load_settings()
        self.settings = base.model_copy(
            update={
                "database_url": db_url,
                "acceleration": 60.0,
                "collector": base.collector.model_copy(
                    update={
                        "opcua_endpoint": f"opc.tcp://127.0.0.1:{port}",
                        "health_file": str(tmp / "collector.health"),
                    }
                ),
            }
        )
        self.sim_stop = asyncio.Event()
        self.sim_task: asyncio.Task | None = None
        self.col_stop = asyncio.Event()
        self.col_task: asyncio.Task | None = None
        self.start_ts = self.sim_cfg.clock.start_ts
        self.last_seen_at_stop: list[datetime] = []

    async def start_sim(self) -> None:
        self.sim_task = asyncio.create_task(serve(self.sim_cfg, self.sim_stop))
        await asyncio.sleep(1.0)

    def start_collector(self) -> None:
        self.col_stop = asyncio.Event()
        collector = Collector(self.settings, self.engine)
        self.col_task = asyncio.create_task(collector.run(self.col_stop))

    async def last_seen(self) -> datetime | None:
        async with self.engine.connect() as conn:
            return (await data_bounds(conn))[1]

    async def run_until_sim(self, minutes_after_start: float, timeout_s: float = 300) -> None:
        target = dt(self.start_ts) + timedelta(minutes=minutes_after_start)
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            assert self.col_task is not None and not self.col_task.done(), self.col_task
            seen = await self.last_seen()
            if seen is not None and seen >= target:
                return
            await asyncio.sleep(0.5)
        raise AssertionError(f"simulated time did not reach +{minutes_after_start} min")

    async def kill_collector(self) -> None:
        """A crash: no flush, no goodbye."""
        assert self.col_task is not None
        self.col_task.cancel()
        await asyncio.gather(self.col_task, return_exceptions=True)
        self.last_seen_at_stop.append(await self.last_seen())  # what the database knows: no more

    async def stop_collector(self) -> None:
        assert self.col_task is not None
        self.col_stop.set()
        await asyncio.wait_for(self.col_task, timeout=20)
        self.last_seen_at_stop.append(await self.last_seen())

    async def stop_sim(self) -> None:
        self.sim_stop.set()
        assert self.sim_task is not None
        await asyncio.wait_for(self.sim_task, timeout=20)


async def rows(engine: AsyncEngine, sql: str) -> list[tuple]:
    async with engine.connect() as conn:
        return [tuple(r) for r in (await conn.execute(text(sql))).all()]


@pytest.fixture
def e2e_db(fresh_db_url: str) -> str:
    migrate.upgrade(fresh_db_url)
    return fresh_db_url


async def test_chaos_simulator_to_database(e2e_db: str, tmp_path: Path) -> None:
    engine = create_async_engine(e2e_db)
    rig = Rig(engine, e2e_db, tmp_path)
    try:
        await rig.start_sim()

        rig.start_collector()
        await rig.run_until_sim(30)
        await rig.kill_collector()  # crash at ~08:30
        await asyncio.sleep(3)  # ~3 simulated minutes nobody is watching
        rig.start_collector()
        await rig.run_until_sim(90)
        await rig.stop_collector()  # clean stop at ~09:30
        await asyncio.sleep(2)
        rig.start_collector()
        await rig.run_until_sim(150)
        await rig.stop_collector()
        await rig.stop_sim()
    except BaseException:
        for task in (rig.col_task, rig.sim_task):
            if task and not task.done():
                task.cancel()
        raise

    end = (await rig.last_seen()) or dt(rig.start_ts)
    try:
        await verify(rig, engine, end)
    finally:
        await engine.dispose()


# ====================================================================== verification


def between(ts: datetime, lo: datetime, hi: datetime) -> bool:
    return lo <= ts <= hi


async def verify(rig: Rig, engine: AsyncEngine, end: datetime) -> None:
    # ---- ground truth: the same simulation, replayed offline
    sim = Simulation(rig.sim_cfg)
    truth = raw(sim.start() + sim.run_until(end.timestamp()))
    groups = as_groups_ts(truth)

    heartbeats = [c.ts for c in truth if c.tag == HEARTBEAT]
    freezes = [
        (a, b)
        for a, b in zip(heartbeats, heartbeats[1:], strict=False)
        if (b - a).total_seconds() > 5
    ]
    expected_stops = ground_truth_stops(groups)
    truth_samples = counter_series(truth)

    all_gaps = await rows(engine, "SELECT start_ts, end_ts FROM comm_gaps ORDER BY start_ts")
    open_gaps = [g for g in all_gaps if g[1] is None]
    assert len(open_gaps) <= 1, f"more than one open gap: {open_gaps}"
    if open_gaps:  # the run ended inside a freeze: the heartbeat really was silent since then
        assert open_gaps[0][0] == heartbeats[-1]
    db_gaps = [g for g in all_gaps if g[1] is not None]

    # ---- where the collector was not looking
    # 1. before it first connected: the PLC had been running for a minute or two already
    startup = [g for g in db_gaps if g[0] == dt(rig.start_ts)]
    assert len(startup) == 1, f"the time before the first observation is not a gap: {db_gaps}"
    windows: list[tuple[datetime, datetime]] = [startup[0]]
    # 2. each restart, the crash and the clean stop: a gap that covers it
    restarts: list[tuple[datetime, datetime]] = []
    for seen in rig.last_seen_at_stop[:2]:
        covering = [g for g in db_gaps if g[0] <= seen < g[1]]
        assert len(covering) == 1, f"no gap covers the collector being down at {seen}: {db_gaps}"
        restarts.append(covering[0])
    windows += restarts
    # the crash may lose up to a flush interval of progress: the gap starts a bit earlier, never later
    assert restarts[0][0] <= rig.last_seen_at_stop[0]
    assert (restarts[0][1] - restarts[0][0]).total_seconds() >= 3 * 60  # 3 s of downtime, at 60x

    def in_window(ts: datetime) -> bool:
        return any(between(ts, a, b) for a, b in windows)

    # ---- chaos: every heartbeat freeze the collector was watching is a recorded gap
    watched = [(a, b) for a, b in freezes if not in_window(a) and not in_window(b)]
    assert len(watched) >= 2, f"expected at least two freezes in the run, got {freezes}"
    for a, b in watched:
        assert (a, b) in db_gaps, f"freeze {a} -> {b} was not recorded as a gap"
        assert (b - a).total_seconds() >= FREEZE_S
    # nothing else is a gap: no false positive from the watchdog
    unexpected = [
        g
        for g in db_gaps
        if g not in freezes
        and g not in windows
        and not any(w[0] <= g[0] and g[1] <= w[1] for w in windows)
    ]
    assert unexpected == [], f"communication loss declared without reason: {unexpected}"

    # ---- stops: exactly what the PLC did, no phantom, none invented by the restarts
    db_stops = {
        r[0]: r
        for r in await rows(
            engine,
            "SELECT start_ts, end_ts, state, fault_code, origin_station, suggested_category, duration_s "
            "FROM stops ORDER BY start_ts",
        )
    }
    assert len(expected_stops) >= 4
    assert set(db_stops) <= set(expected_stops), "phantom stop"
    for start, want in expected_stops.items():
        lost = (
            in_window(start)
            and want["end"] is not None
            and want["end"] < windows_end(start, windows)
        )
        if lost:
            continue
        assert start in db_stops, f"stop {start} ({want}) is missing"
        row = db_stops[start]
        assert (row[2], row[3]) == (want["state"], want["fault"])
        if not in_window(start) and (want["end"] is None or not in_window(want["end"])):
            assert row[1] == want["end"], f"stop {start}: ends {row[1]}, PLC says {want['end']}"
            if want["end"] is not None:
                assert row[6] == pytest.approx((want["end"] - start).total_seconds(), abs=1e-3)
    open_stops = [r for r in db_stops.values() if r[1] is None]
    assert len(open_stops) <= 1
    causes = {r[2]: r[5] for r in db_stops.values()}
    assert causes.get(CellState.PLANNED_STOP) is None  # a break needs no qualification
    assert any(r[2] == CellState.PLANNED_STOP and r[4] == "CELL" for r in db_stops.values())

    # ---- counters: raw samples, all genuine, complete where we were watching
    db_samples = await rows(
        engine, "SELECT ts, good_total, scrap_total FROM counter_samples ORDER BY ts"
    )
    db_set = {(ts, g, s) for ts, g, s in db_samples}
    assert db_set <= truth_samples, "a counter sample the PLC never produced"
    missing = sorted(truth_samples - db_set)
    assert all(in_window(ts) for ts, _, _ in missing), (
        f"counter samples lost outside the outages: {[m for m in missing if not in_window(m[0])][:5]} "
        f"windows={windows}"
    )
    assert len(db_samples) > 100  # ~150 parts in 2.5 simulated hours, minus the outages

    # ---- rollover, reset and freeze, as the OEE sees them
    deltas = counter_deltas([CounterSample(ts, g, s, 55.0) for ts, g, s in db_samples])
    kinds = [d.kind for d in deltas]
    assert DeltaKind.ROLLOVER in kinds, "the UInt32 rollover was not seen"
    assert kinds.count(DeltaKind.RESET) >= 1, "the hourly counter reset was not seen"
    assert all(d.good >= 0 and d.scrap >= 0 for d in deltas)  # never negative production
    big = [d for d in deltas if d.good > 1000 or d.scrap > 1000]
    assert big == [], f"a wrap or a reset was counted as production: {big}"

    async with engine.connect() as conn:
        by_shift = await oee_over_range(conn, rig.settings.oee, dt(rig.start_ts), end, "shift")
        by_day = await oee_over_range(conn, rig.settings.oee, dt(rig.start_ts), end, "day")
        whole = await oee_over_range(conn, rig.settings.oee, dt(rig.start_ts), end)
    for r in (*by_shift, *by_day, *whole):
        check_invariant(r)  # the reference invariant on data that came through the whole chain
    total = whole[0]
    assert total.dropped_parts > 0  # parts made during the freezes are excluded, with the time
    assert total.gap_s >= FREEZE_S * len(watched)
    assert 0 < total.planned_stop_s <= 20 * 60
    assert total.oee is not None and 0.3 < total.oee < 1.0
    assert total.availability is not None and 0.5 < total.availability <= 1.0
    assert combine(by_shift).oee == pytest.approx(total.oee)
    assert total.anomalies == ()
    assert total.micro_s > 0  # micro-stops exist and stay inside operating time


def windows_end(ts: datetime, windows: list[tuple[datetime, datetime]]) -> datetime:
    return next(b for a, b in windows if between(ts, a, b))


def ground_truth_stops(groups) -> dict[datetime, dict]:
    stops: dict[datetime, dict] = {}
    state = fault = None
    open_start = None
    for g in groups:
        if not {CELL_STATE, CELL_FAULT, CELL_ORIGIN} & set(g.values):
            continue
        state = g.values.get(CELL_STATE, state)
        fault = g.values.get(CELL_FAULT, fault)
        if open_start is not None:
            stops[open_start]["end"] = g.ts
            open_start = None
        if state != CellState.PRODUCING:
            stops[g.ts] = {"end": None, "state": state, "fault": fault}
            open_start = g.ts
    return stops


def counter_series(truth) -> set[tuple[datetime, int, int]]:
    """Counter samples as the collector should store them (one per change of either counter)."""
    out: set[tuple[datetime, int, int]] = set()
    good = scrap = None
    for g in as_groups_ts(truth):
        if GOOD in g.values or SCRAP in g.values:
            good = g.values.get(GOOD, good)
            scrap = g.values.get(SCRAP, scrap)
            out.add((g.ts, int(good), int(scrap)))
    return out
