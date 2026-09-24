from __future__ import annotations

import statistics

import pytest
from conftest import DAY, HOUR, make_config, quiet_config, replay, segments

from cell_sim.config import SimConfig
from cell_sim.engine import Simulation
from cell_sim.model import FAULTS, STATIONS, TAGS, CellState, TagChange

START = make_config().clock.start_ts


def at(hh: int, mm: int = 0, ss: int = 0) -> float:
    """Simulated time of day on the first simulated day (start is 05:00)."""
    return START + (hh - 5) * HOUR + mm * 60 + ss


def run(cfg: SimConfig, until: float, inject: list[tuple[float, str, tuple]] | None = None):
    """Run a simulation, optionally injecting forced events: (time, method, args) on the cell."""
    sim = Simulation(cfg)
    for ts, method, args in inject or []:
        sim.register(f"inject_{method}_{ts}", lambda p, e, m=method: getattr(sim.cell, m)(*p))
        sim.schedule(ts - sim.now, f"inject_{method}_{ts}", payload=args)
    changes = sim.start() + sim.run_until(until)
    return sim, changes


# ------------------------------------------------------------------ invariants over a long run


@pytest.fixture(scope="module")
def three_days() -> tuple[SimConfig, list[TagChange]]:
    cfg = make_config()
    _, changes = run(cfg, START + 3 * DAY)
    return cfg, changes


def test_every_tag_is_published_at_start(three_days: tuple[SimConfig, list[TagChange]]) -> None:
    _, changes = three_days
    first_ts = changes[0].ts
    initial = {c.path for c in changes if c.ts == first_ts}
    assert initial == set(TAGS)


def test_time_is_monotonic_and_simulated(three_days: tuple[SimConfig, list[TagChange]]) -> None:
    cfg, changes = three_days
    stamps = [c.ts for c in changes]
    assert stamps == sorted(stamps)
    assert stamps[0] == cfg.clock.start_ts
    assert stamps[-1] <= cfg.clock.start_ts + 3 * DAY


def test_state_fault_and_mode_are_coherent(three_days: tuple[SimConfig, list[TagChange]]) -> None:
    for _, img in replay(three_days[1]):
        state = CellState(img["Cell/State"])
        code = img["Cell/FaultCode"]
        assert (state is CellState.FAULT) == (code != 0)
        assert (img["Cell/Mode"] == 2) == (state is CellState.MANUAL)
        for st in STATIONS:
            assert img[f"{st}/State"] == state
            expected = code if code in FAULTS and FAULTS[code].station == st else 0
            assert img[f"{st}/FaultCode"] == expected


def test_counters_and_heartbeat_never_go_backwards(
    three_days: tuple[SimConfig, list[TagChange]],
) -> None:
    last = {"Cell/GoodCount": 0, "Cell/ScrapCount": 0, "Cell/Heartbeat": 0}
    for c in three_days[1]:
        if c.path in last:
            assert c.value >= last[c.path]
            if c.path == "Cell/Heartbeat":
                assert c.value == last[c.path] + 1 or last[c.path] == 0
            last[c.path] = int(c.value)
    assert last["Cell/GoodCount"] > 3000
    assert last["Cell/Heartbeat"] >= 3 * 86_400 - 1  # one tick per simulated second


def test_weld_points_and_cap_change(three_days: tuple[SimConfig, list[TagChange]]) -> None:
    cfg, changes = three_days
    points = [int(c.value) for c in changes if c.path == "OP20/Weld/PointsSinceCapChange"]
    resets = [i for i in range(1, len(points)) if points[i] < points[i - 1]]
    assert points[0] == 0
    assert all(b - a in (0, 1) or b == 0 for a, b in zip(points, points[1:], strict=False))
    assert len(resets) >= 2  # ~1500 parts per cap, ~4500 parts in 3 days
    # A cap change is about 1500 parts (+-5 %, minus/plus the interval granularity).
    cap_points = cfg.weld.cap_change.every_parts * cfg.weld.points_per_part
    for i in resets:
        assert 0.9 * cap_points <= points[i - 1] <= 1.1 * cap_points


def test_actual_cycle_is_slower_than_theoretical(
    three_days: tuple[SimConfig, list[TagChange]],
) -> None:
    cycles = [float(c.value) for c in three_days[1] if c.path == "Cell/LastCycleTime" and c.value]
    assert min(cycles) > 55  # noise is strictly positive: a performance loss
    assert 56.0 < statistics.mean(cycles) < 57.2


def test_all_states_and_stop_kinds_occur(three_days: tuple[SimConfig, list[TagChange]]) -> None:
    cfg, changes = three_days
    segs = segments(changes, START + 3 * DAY)
    assert {s.state for s in segs} == set(CellState)
    setups = [s.duration for s in segs if s.state is CellState.SETUP]
    assert any(abs(d - 30) < 1e-6 for d in setups)  # tip dressing: micro-stop
    assert any(abs(d - 300) < 1e-6 for d in setups)  # cap change
    faults = [s for s in segs if s.state is CellState.FAULT]
    assert all(300 <= s.duration <= 45 * 60 for s in faults)


def test_same_seed_same_run_different_seed_different_run() -> None:
    end = START + 6 * HOUR
    a = run(make_config(), end)[1]
    b = run(make_config(), end)[1]
    c = run(make_config(seed=7), end)[1]
    assert a == b
    assert a != c


def test_stop_origin_follows_the_stop(three_days: tuple[SimConfig, list[TagChange]]) -> None:
    last_fault_origin = 0
    for _, img in replay(three_days[1]):
        state = CellState(img["Cell/State"])
        code = img["Cell/FaultCode"]
        origin = img["Cell/StopOrigin"]
        if state is CellState.FAULT:
            last_fault_origin = origin
            assert origin == {"OP10": 10, "OP20": 20, "OP30": 30, "CELL": 99}[FAULTS[code].station]
        elif state is CellState.MANUAL:
            assert origin == last_fault_origin  # recovery happens where the fault was
        else:
            assert (
                origin
                == {
                    CellState.PRODUCING: 0,
                    CellState.PLANNED_STOP: 99,
                    CellState.SETUP: 20,
                    CellState.STARVED: 10,
                    CellState.BLOCKED: 30,
                }[state]
            )


def test_stop_origin_is_written_with_the_state_timestamp(
    three_days: tuple[SimConfig, list[TagChange]],
) -> None:
    changes = three_days[1]
    state_ts = {c.ts for c in changes if c.path == "Cell/State"}
    origin_ts = {c.ts for c in changes if c.path == "Cell/StopOrigin"}
    assert origin_ts <= state_ts  # the origin never changes on its own, always with the state


# ------------------------------------------------------------------ planned breaks


def test_break_starts_at_end_of_cycle_and_ends_on_schedule() -> None:
    _, changes = run(quiet_config(), START + DAY)
    breaks = [s for s in segments(changes, START + DAY) if s.state is CellState.PLANNED_STOP]
    assert len(breaks) == 3  # 09:00, 17:00, 01:00
    for s, scheduled in zip(breaks, (at(9), at(17), at(25)), strict=True):
        assert scheduled <= s.start < scheduled + 70  # waits for the part in progress (<~60 s)
        assert s.end == pytest.approx(scheduled + 20 * 60, abs=1e-6)  # never shifted


def test_fault_in_progress_shortens_the_break() -> None:
    # Fault from 08:55 to 09:05 straddles the break start (09:00).
    cfg = quiet_config()
    inject = [(at(8, 55), "_enter_fault", (101, 600.0))]
    _, changes = run(cfg, at(10), inject)
    segs = [s for s in segments(changes, at(10)) if at(8, 50) < s.start < at(9, 30)]

    fault = next(s for s in segs if s.state is CellState.FAULT)
    assert fault.fault_code == 101
    assert fault.start == pytest.approx(at(8, 55))
    assert fault.end == pytest.approx(at(9, 5))  # the fault carries on through the break start
    brk = next(s for s in segs if s.state is CellState.PLANNED_STOP)
    assert brk.start == pytest.approx(at(9, 5))  # break resumes where the fault ends...
    assert brk.end == pytest.approx(at(9, 20))  # ...and still ends on schedule (shortened)


def test_fault_covering_the_whole_break_leaves_no_planned_stop() -> None:
    inject = [(at(8, 58), "_enter_fault", (102, 30 * 60.0))]  # until 09:28, break is 09:00-09:20
    _, changes = run(quiet_config(), at(10), inject)
    segs = [s for s in segments(changes, at(10)) if at(8, 55) < s.start < at(9, 40)]
    assert [s.state for s in segs] == [CellState.FAULT, CellState.PRODUCING]
    assert segs[0].end == pytest.approx(at(9, 28))


def test_starved_line_is_taken_over_by_the_break() -> None:
    def starve(cell) -> None:  # forced starved period covering the break start
        cell._enter(CellState.STARVED)
        cell._stop_for(3600.0, "starved")

    sim = Simulation(quiet_config())
    sim.register("inject", lambda p, e: starve(sim.cell))
    sim.schedule(at(8, 59) - sim.now, "inject")
    changes = sim.start() + sim.run_until(at(10))
    segs = [s for s in segments(changes, at(10)) if at(8, 55) < s.start < at(9, 40)]
    assert [s.state for s in segs] == [
        CellState.STARVED,
        CellState.PLANNED_STOP,
        CellState.PRODUCING,
    ]
    assert segs[0].end == pytest.approx(at(9))
    assert segs[1].end == pytest.approx(at(9, 20))


def test_dressing_due_during_a_break_is_deferred_not_dropped() -> None:
    def mark_due(cell) -> None:
        cell._dressing_due = True

    sim = Simulation(quiet_config())
    sim.register("inject", lambda p, e: mark_due(sim.cell))
    sim.schedule(at(9, 5) - sim.now, "inject")  # during the 09:00-09:20 break
    changes = sim.start() + sim.run_until(at(10))
    segs = [s for s in segments(changes, at(10)) if at(9) <= s.start < at(9, 40)]
    assert [s.state for s in segs][:2] == [CellState.PLANNED_STOP, CellState.SETUP]
    assert segs[1].start == pytest.approx(at(9, 20))
    assert segs[1].duration == pytest.approx(30)


# ------------------------------------------------------------------ fault 201 and manual mode


def test_fault_201_after_two_bad_parts_then_dressing() -> None:
    # A tolerance of ~0 makes every weld point NOK.
    cfg = quiet_config(weld={"tolerance_pct": 0.001})
    _, changes = run(cfg, START + 2 * HOUR)
    segs = segments(changes, START + 2 * HOUR)
    first_fault = next(s for s in segs if s.state is CellState.FAULT)
    assert first_fault.fault_code == 201
    assert 5 * 60 <= first_fault.duration <= 20 * 60
    # Two parts of ~56 s were made before the fault.
    assert 2 * 55 <= first_fault.start - START <= 2 * 60
    after = segs[segs.index(first_fault) + 1]
    assert after.state is CellState.SETUP
    assert after.duration == pytest.approx(30)  # dressing, a micro-stop (< 120 s)
    assert first_fault.end == pytest.approx(after.start)

    img = next(i for ts, i in replay(changes) if ts == first_fault.start)
    assert img["OP20/FaultCode"] == 201
    assert img["OP10/FaultCode"] == img["OP30/FaultCode"] == 0
    assert img["OP20/Weld/LastPointOK"] is False


def test_manual_recovery_after_selected_faults_only() -> None:
    cfg = quiet_config(manual={"probability": 1.0})
    inject = [
        (at(6), "_enter_fault", (202, 600.0)),  # in the manual list
        (at(7), "_enter_fault", (101, 600.0)),  # not in the list
    ]
    _, changes = run(cfg, at(8), inject)
    segs = segments(changes, at(8))
    i202 = next(i for i, s in enumerate(segs) if s.fault_code == 202)
    assert segs[i202 + 1].state is CellState.MANUAL
    assert 2 * 60 <= segs[i202 + 1].duration <= 10 * 60
    i101 = next(i for i, s in enumerate(segs) if s.fault_code == 101)
    assert segs[i101 + 1].state is CellState.PRODUCING
    modes = {(img["Cell/State"], img["Cell/Mode"]) for _, img in replay(changes)}
    assert (int(CellState.MANUAL), 2) in modes


def test_cycle_is_paused_by_a_stop_and_resumes_where_it_left() -> None:
    # No part may be lost or duplicated across a fault: counters keep counting parts made.
    inject = [(at(6, 0, 30), "_enter_fault", (103, 400.0))]
    _, changes = run(quiet_config(cell={"base_scrap_rate": 0}), at(7), inject)
    good = [c for c in changes if c.path == "Cell/GoodCount"]
    gaps = [b.ts - a.ts for a, b in zip(good, good[1:], strict=False)]
    assert max(gaps) >= 400  # the fault delays a part by its duration
    assert min(gaps) >= 55


# ------------------------------------------------------------------ fault Pareto


@pytest.mark.slow
def test_fault_time_is_concentrated_on_202_101_303() -> None:
    """Realistic Pareto: three dominant codes, ~6 % of the time in fault overall."""
    days = 42
    cfg = make_config()
    _, changes = run(cfg, START + days * DAY)
    by_code: dict[int, float] = {}
    for s in segments(changes, START + days * DAY):
        if s.state is CellState.FAULT:
            by_code[s.fault_code] = by_code.get(s.fault_code, 0.0) + s.duration
    total = sum(by_code.values())
    top3 = sorted(by_code, key=by_code.__getitem__, reverse=True)[:3]

    assert set(top3) == {202, 101, 303}
    assert 0.60 <= sum(by_code[c] for c in top3) / total <= 0.70
    assert 0.05 <= total / (days * DAY) <= 0.075
    assert set(by_code) == set(cfg.faults) | {201}  # every code shows up over six weeks


# ------------------------------------------------------------------ recording aid: forced fault


def test_forced_fault_goes_through_the_normal_fault_path() -> None:
    cfg = quiet_config()
    sim = Simulation(cfg)
    sim.start()
    sim.run_until(START + 600)
    assert sim.image["Cell/State"] == int(CellState.PRODUCING)

    at_ts = START + 600.5
    assert sim.next_event_time > at_ts
    changes = sim.force_fault(202, 300.0, at_ts)
    assert changes is not None
    img = sim.image
    assert img["Cell/State"] == int(CellState.FAULT)
    assert img["Cell/FaultCode"] == 202
    assert img["Cell/StopOrigin"] == 20
    assert all(c.ts == at_ts for c in changes)

    sim.run_until(at_ts + 302)
    assert sim.image["Cell/FaultCode"] == 0


def test_forced_fault_is_refused_when_not_producing_or_time_would_go_back() -> None:
    sim = Simulation(quiet_config())
    sim.start()
    sim.run_until(START + 600)
    assert sim.force_fault(202, 300.0, sim.next_event_time + 1) is None  # events still due
    assert sim.force_fault(9999, 300.0, START + 601) is None  # unknown fault code
