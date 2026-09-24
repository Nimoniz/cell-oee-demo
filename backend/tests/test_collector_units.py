"""Assembler, tracker and watchdog: pure logic, no database, no network."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest
from conftest import t
from sim_helpers import raw, sim_changes, sim_config

from app.collector.assembler import ChangeAssembler
from app.collector.tracker import StopTracker, TrackerState
from app.collector.types import (
    CELL_FAULT,
    CELL_ORIGIN,
    CELL_STATE,
    GOOD,
    HEARTBEAT,
    LAST_CYCLE,
    SCRAP,
    TCT,
    WELD_CURRENT,
    WELD_OK,
    WELD_POINTS,
    ClosedGapOp,
    CloseGapOp,
    CloseStopOp,
    CounterSampleOp,
    Group,
    OpenGapOp,
    OpenStopOp,
    RawChange,
    StateEventOp,
    WeldSampleOp,
)
from app.collector.watchdog import HeartbeatWatchdog
from app.config import Settings
from app.domain import CellState

# ====================================================================== assembler


def rc(tag: str, value, ts: datetime) -> RawChange:
    return RawChange(tag, value, ts)


def test_assembler_groups_by_source_timestamp_and_orders_by_it() -> None:
    a = ChangeAssembler(settle_s=1.0)
    # Notifications arrive in any order: newest first, tags of one instant in two batches.
    assert a.feed([rc(GOOD, 10, t(6, 0, 2))], now=0.0) == []
    assert a.feed([rc(CELL_STATE, 3, t(6, 0, 5))], now=0.4) == []
    assert a.feed([rc(CELL_FAULT, 202, t(6, 0, 5))], now=0.6) == []  # straggler of the same instant
    groups = a.poll(now=1.2)  # the (6:00:02) group is old enough, the (6:00:05) one is not
    assert [(g.ts, g.values) for g in groups] == [(t(6, 0, 2), {GOOD: 10})]
    groups = a.poll(now=1.5)
    assert [(g.ts, g.values) for g in groups] == [(t(6, 0, 5), {CELL_STATE: 3, CELL_FAULT: 202})]


def test_assembler_never_lets_a_newer_group_overtake_an_older_one() -> None:
    a = ChangeAssembler(settle_s=1.0)
    a.feed([rc(GOOD, 1, t(6, 0, 10))], now=0.0)
    a.feed([rc(SCRAP, 1, t(6, 0, 5))], now=0.9)  # older timestamp, arrived later
    assert a.poll(now=1.5) == []  # blocked by the younger, older-timestamp group
    released = a.poll(now=2.0)
    assert [g.ts for g in released] == [t(6, 0, 5), t(6, 0, 10)]


def test_assembler_flags_what_arrives_after_its_timestamp_was_released() -> None:
    a = ChangeAssembler(settle_s=0.5)
    a.feed([rc(GOOD, 1, t(6))], now=0.0)
    a.poll(now=1.0)
    late = a.feed([rc(SCRAP, 1, t(6)), rc(GOOD, 0, t(5, 59))], now=2.0)
    assert [(g.ts, g.late) for g in late] == [(t(5, 59), True), (t(6), True)]


def test_assembler_flush_releases_everything_in_order() -> None:
    a = ChangeAssembler(settle_s=60.0)
    a.feed([rc(GOOD, 2, t(6, 0, 9)), rc(GOOD, 1, t(6, 0, 3))], now=0.0)
    assert [g.ts for g in a.flush()] == [t(6, 0, 3), t(6, 0, 9)]
    assert a.oldest_pending is None


# ====================================================================== fake database


@dataclass
class FakeDb:
    """The semantics of CollectorStore (natural keys, ON CONFLICT DO NOTHING), in memory."""

    events: dict[tuple[str, datetime], tuple] = field(default_factory=dict)
    stops: dict[datetime, dict] = field(default_factory=dict)
    counters: dict[datetime, tuple] = field(default_factory=dict)
    welds: dict[datetime, tuple] = field(default_factory=dict)
    gaps: dict[datetime, datetime | None] = field(default_factory=dict)

    def apply(self, ops) -> None:
        for op in ops:
            match op:
                case StateEventOp():
                    self.events.setdefault(
                        (op.scope, op.ts), (op.state, op.fault_code, op.stop_origin)
                    )
                case OpenStopOp():
                    assert not any(s["end"] is None for s in self.stops.values()), "two open stops"
                    self.stops.setdefault(
                        op.start_ts,
                        {
                            "end": None,
                            "state": op.state,
                            "origin": op.origin_station,
                            "fault": op.fault_code,
                            "suggested": op.suggested_category,
                        },
                    )
                case CloseStopOp():
                    stop = self.stops.get(op.start_ts)
                    if stop and stop["end"] is None and op.start_ts < op.end_ts:
                        stop["end"] = op.end_ts
                case CounterSampleOp():
                    self.counters.setdefault(
                        op.ts, (op.good, op.scrap, op.theoretical_cycle_s, op.last_cycle_s)
                    )
                case WeldSampleOp():
                    self.welds.setdefault(op.ts, (op.current_ka, op.points_since_cap_change, op.ok))
                case OpenGapOp():
                    self.gaps.setdefault(op.start_ts, None)
                case CloseGapOp() if self.gaps.get(op.start_ts, 1) is None:
                    self.gaps[op.start_ts] = op.end_ts
                case ClosedGapOp():
                    self.gaps.setdefault(op.start_ts, op.end_ts)

    def resume(self) -> TrackerState:
        s = TrackerState()
        cell = sorted((k for k in self.events if k[0] == "CELL"), key=lambda k: k[1])
        if cell:
            ts = cell[-1][1]
            s.last_cell = (ts, *self.events[("CELL", ts)])
        for scope in ("OP10", "OP20", "OP30"):
            evs = sorted(k for k in self.events if k[0] == scope)
            if evs:
                ts = evs[-1][1]
                st, fault, _ = self.events[(scope, ts)]
                s.last_station[scope] = (ts, st, fault)
        opens = [start for start, st in self.stops.items() if st["end"] is None]
        s.open_stop_start = opens[0] if opens else None
        s.last_counter_ts = max(self.counters, default=None)
        s.last_weld_ts = max(self.welds, default=None)
        return s

    def snapshot(self) -> tuple:
        return (
            dict(self.events),
            {k: dict(v) for k, v in self.stops.items()},
            dict(self.counters),
            dict(self.welds),
            dict(self.gaps),
        )


def as_groups(changes: list[RawChange]) -> list[Group]:
    by_ts: dict[datetime, Group] = {}
    for c in changes:
        by_ts.setdefault(c.ts, Group(c.ts)).values[c.tag] = c.value
    return [by_ts[ts] for ts in sorted(by_ts)]


def feed(tracker: StopTracker, db: FakeDb, changes: list[RawChange]) -> list:
    all_ops = []
    for group in as_groups([c for c in changes if c.tag != HEARTBEAT]):
        ops = tracker.apply(group)
        db.apply(ops)
        all_ops += ops
    return all_ops


def current_snapshot(changes: list[RawChange]) -> list[RawChange]:
    """What a subscription sends when it is created: each tag's current value, with the
    SourceTimestamp of its last change."""
    last: dict[str, RawChange] = {}
    for c in changes:
        last[c.tag] = c
    return list(last.values())


def initial_image(ts: datetime, state=1, fault=0, origin=0, good=0, scrap=0) -> list[RawChange]:
    values = {
        CELL_STATE: state,
        CELL_FAULT: fault,
        CELL_ORIGIN: origin,
        "Cell/Mode": 1,
        GOOD: good,
        SCRAP: scrap,
        TCT: 55.0,
        LAST_CYCLE: 0.0,
        WELD_CURRENT: 0.0,
        WELD_POINTS: 0,
        WELD_OK: True,
        **{f"{s}/State": state for s in ("OP10", "OP20", "OP30")},
        **{f"{s}/FaultCode": 0 for s in ("OP10", "OP20", "OP30")},
    }
    return [rc(tag, v, ts) for tag, v in values.items()]


# ====================================================================== tracker


def test_first_snapshot_records_state_without_inventing_a_stop() -> None:
    db, tracker = FakeDb(), StopTracker()
    feed(tracker, db, initial_image(t(5)))
    assert {k for k in db.events if k[0] == "CELL"} == {("CELL", t(5))}
    assert db.events[("CELL", t(5))] == (1, 0, 0)
    assert db.stops == {}


def test_a_stop_opens_and_closes_at_the_source_timestamps() -> None:
    db, tracker = FakeDb(), StopTracker()
    feed(tracker, db, initial_image(t(5)))
    feed(
        tracker,
        db,
        [
            rc(CELL_STATE, 3, t(6, 10, 0.5)),
            rc(CELL_FAULT, 202, t(6, 10, 0.5)),
            rc(CELL_ORIGIN, 20, t(6, 10, 0.5)),
        ],
    )
    assert db.stops[t(6, 10, 0.5)] == {
        "end": None,
        "state": 3,
        "origin": "OP20",
        "fault": 202,
        "suggested": "electrical_breakdown",
    }
    feed(
        tracker,
        db,
        [
            rc(CELL_STATE, 1, t(6, 30, 2)),
            rc(CELL_FAULT, 0, t(6, 30, 2)),
            rc(CELL_ORIGIN, 0, t(6, 30, 2)),
        ],
    )
    assert db.stops[t(6, 10, 0.5)]["end"] == t(6, 30, 2)
    assert len(db.stops) == 1


def test_fault_code_is_taken_from_the_latest_value_not_only_from_the_changed_tags() -> None:
    """Going PRODUCING -> STARVED changes State and StopOrigin, not FaultCode (still 0)."""
    db, tracker = FakeDb(), StopTracker()
    feed(tracker, db, initial_image(t(5)))
    feed(tracker, db, [rc(CELL_STATE, 5, t(6)), rc(CELL_ORIGIN, 10, t(6))])
    assert db.events[("CELL", t(6))] == (5, 0, 10)
    assert db.stops[t(6)]["origin"] == "OP10" and db.stops[t(6)]["suggested"] == "missing_parts"


@pytest.mark.parametrize(
    ("state", "origin", "station", "cause"),
    [
        (CellState.PLANNED_STOP, 99, "CELL", None),
        (CellState.SETUP, 20, "OP20", "tool_change"),
        (CellState.BLOCKED, 30, "OP30", "downstream_saturation"),
        (CellState.MANUAL, 30, "OP30", "other"),
    ],
)
def test_origin_is_read_from_the_plc_and_causes_are_suggested(
    state, origin, station, cause
) -> None:
    db, tracker = FakeDb(), StopTracker()
    feed(tracker, db, initial_image(t(5)))
    feed(tracker, db, [rc(CELL_STATE, int(state), t(6)), rc(CELL_ORIGIN, origin, t(6))])
    stop = db.stops[t(6)]
    assert (stop["origin"], stop["suggested"]) == (station, cause)


def test_delivering_the_same_notifications_twice_changes_nothing() -> None:
    changes = raw(sim_changes(sim_config(), hours=3))
    db1, db2 = FakeDb(), FakeDb()
    feed(StopTracker(), db1, changes)
    tracker = StopTracker()
    feed(tracker, db2, changes)
    feed(tracker, db2, changes)  # everything delivered again
    assert db1.snapshot() == db2.snapshot()


def test_a_snapshot_at_first_start_during_a_stop_opens_it_at_its_true_start() -> None:
    db, tracker = FakeDb(), StopTracker()
    # The collector starts at 09:00 while the cell has been faulted since 08:47:12.
    image = initial_image(t(5))
    image = [c for c in image if c.tag not in (CELL_STATE, CELL_FAULT, CELL_ORIGIN)]
    image += [
        rc(CELL_STATE, 3, t(8, 47, 12)),
        rc(CELL_FAULT, 101, t(8, 47, 12)),
        rc(CELL_ORIGIN, 10, t(8, 47, 12)),
    ]
    feed(tracker, db, image)
    assert list(db.stops) == [t(8, 47, 12)]
    assert db.stops[t(8, 47, 12)]["end"] is None


def test_restart_during_a_stop_neither_duplicates_nor_invents_anything() -> None:
    db, tracker = FakeDb(), StopTracker()
    stream = initial_image(t(5)) + [
        rc(CELL_STATE, 3, t(6)),
        rc(CELL_FAULT, 102, t(6)),
        rc(CELL_ORIGIN, 10, t(6)),
    ]
    feed(tracker, db, stream)
    before = db.snapshot()

    restarted = StopTracker(db.resume())  # the collector restarts
    ops = feed(restarted, db, current_snapshot(stream))  # and the subscription replays the values
    assert ops == []
    assert db.snapshot() == before  # one stop, still open, from its real start


def test_state_changes_missed_while_down_close_the_stop_at_their_true_timestamp() -> None:
    db, tracker = FakeDb(), StopTracker()
    feed(
        tracker,
        db,
        initial_image(t(5))
        + [
            rc(CELL_STATE, 3, t(6)),
            rc(CELL_FAULT, 202, t(6)),
            rc(CELL_ORIGIN, 20, t(6)),
        ],
    )
    restarted = StopTracker(db.resume())
    # Down from 06:05 to 06:50; the fault ended at 06:20 and the cell is now producing.
    feed(
        restarted,
        db,
        [
            rc(CELL_STATE, 1, t(6, 20)),
            rc(CELL_FAULT, 0, t(6, 20)),
            rc(CELL_ORIGIN, 0, t(6, 20)),
            rc(GOOD, 5, t(6, 49)),
        ],
    )
    assert db.stops[t(6)]["end"] == t(6, 20)  # the real end, from the source timestamp


def test_older_or_conflicting_events_never_rewrite_history(caplog) -> None:
    db, tracker = FakeDb(), StopTracker()
    feed(tracker, db, initial_image(t(5)) + [rc(CELL_STATE, 5, t(6)), rc(CELL_ORIGIN, 10, t(6))])
    before = db.snapshot()
    with caplog.at_level(logging.WARNING):
        feed(tracker, db, [rc(CELL_STATE, 3, t(5, 30))])  # older than what is stored
        feed(tracker, db, [rc(CELL_STATE, 6, t(6)), rc(CELL_ORIGIN, 30, t(6))])  # same instant
    assert db.snapshot() == before
    assert "ignoring cell event" in caplog.text


def test_station_events_follow_the_cell_and_are_idempotent() -> None:
    db, tracker = FakeDb(), StopTracker()
    stream = initial_image(t(5)) + [
        rc("OP20/State", 3, t(6)),
        rc("OP20/FaultCode", 202, t(6)),
    ]
    feed(tracker, db, stream)
    assert db.events[("OP20", t(6))] == (3, 202, None)
    assert ("OP10", t(6)) not in db.events
    feed(StopTracker(db.resume()), db, current_snapshot(stream))
    assert len([k for k in db.events if k[0] == "OP20"]) == 2


def test_counter_samples_carry_the_theoretical_cycle_time_and_wait_for_all_values() -> None:
    db, tracker = FakeDb(), StopTracker()
    feed(tracker, db, [rc(GOOD, 7, t(6))])  # TCT and the rest are not known yet
    assert db.counters == {}
    feed(tracker, db, [rc(SCRAP, 0, t(5)), rc(TCT, 55.0, t(5)), rc(LAST_CYCLE, 56.2, t(6))])
    feed(tracker, db, [rc(GOOD, 8, t(6, 1)), rc(LAST_CYCLE, 56.9, t(6, 1))])
    assert db.counters == {t(6, 1): (8, 0, 55.0, 56.9)}
    feed(tracker, db, [rc(GOOD, 0, t(6, 2)), rc(SCRAP, 0, t(6, 2))])  # a PLC reset is stored raw
    assert db.counters[t(6, 2)][:2] == (0, 0)


def test_weld_samples_are_one_per_weld_point_not_per_cap_change() -> None:
    db, tracker = FakeDb(), StopTracker()
    feed(tracker, db, initial_image(t(5)))
    db.welds.clear()
    feed(
        tracker,
        db,
        [rc(WELD_CURRENT, 7.9, t(6)), rc(WELD_POINTS, 5, t(6)), rc(WELD_OK, True, t(6))],
    )
    feed(tracker, db, [rc(WELD_POINTS, 0, t(6, 5))])  # cap change: counter reset, no weld point
    feed(tracker, db, [rc(WELD_CURRENT, 8.1, t(6, 10)), rc(WELD_POINTS, 1, t(6, 10))])
    assert db.welds == {t(6): (7.9, 5, True), t(6, 10): (8.1, 1, True)}


# ---------------------------------------------------------------- against the real simulator


def ground_truth_stops(changes: list[RawChange]) -> dict[datetime, dict]:
    """Stops read directly from the simulator's stream, without the tracker."""
    stops: dict[datetime, dict] = {}
    state = fault = origin = None
    open_start = None
    for group in as_groups([c for c in changes if c.tag != HEARTBEAT]):
        if not {CELL_STATE, CELL_FAULT, CELL_ORIGIN} & set(group.values):
            continue
        state = group.values.get(CELL_STATE, state)
        fault = group.values.get(CELL_FAULT, fault)
        origin = group.values.get(CELL_ORIGIN, origin)
        if open_start is not None:
            stops[open_start]["end"] = group.ts
            open_start = None
        if state != CellState.PRODUCING:
            stops[group.ts] = {"end": None, "state": state, "fault": fault, "origin_code": origin}
            open_start = group.ts
    return stops


def test_tracker_reproduces_the_simulators_stops_exactly() -> None:
    changes = raw(sim_changes(sim_config(), hours=48))
    db = FakeDb()
    feed(StopTracker(), db, changes)
    truth = ground_truth_stops(changes)
    assert len(truth) > 40
    assert set(db.stops) == set(truth)
    for start, want in truth.items():
        got = db.stops[start]
        assert (got["end"], got["state"], got["fault"]) == (
            want["end"],
            want["state"],
            want["fault"],
        )
    assert db.gaps == {}


def test_restarting_the_collector_at_any_point_gives_the_same_database() -> None:
    """The idempotence property: cut the stream anywhere, restart from the stored state, let the
    subscription replay the current values, carry on: the result is identical."""
    changes = raw(sim_changes(sim_config(), hours=36))
    reference = FakeDb()
    feed(StopTracker(), reference, changes)

    groups = as_groups(changes)
    rng = random.Random(1)
    cuts = sorted(rng.sample(range(1, len(groups)), 40))
    # Also cut exactly inside stops and around transitions.
    cuts += [i for i, g in enumerate(groups) if CELL_STATE in g.values][5:60:4]
    for cut in cuts:
        db, tracker = FakeDb(), StopTracker()
        head = [c for g in groups[:cut] for c in _changes(g)]
        tail = [c for g in groups[cut:] for c in _changes(g)]
        feed(tracker, db, head)
        restarted = StopTracker(db.resume())
        feed(restarted, db, current_snapshot(head))  # replay on subscription
        feed(restarted, db, tail)
        assert db.snapshot() == reference.snapshot(), f"restart after group {cut}"


def _changes(group: Group) -> list[RawChange]:
    return [RawChange(tag, value, group.ts) for tag, value in group.values.items()]


# ====================================================================== watchdog

T0 = t(6)


def test_watchdog_timeout_is_max_of_wall_floor_and_simulated_time() -> None:
    def timeout(acceleration: float) -> float:
        cfg = {"oee": {"shifts": [{"name": "a", "start": "05:00", "end": "05:00"}]}}
        return Settings.model_validate({**cfg, "acceleration": acceleration}).heartbeat_timeout_s

    assert timeout(1) == 5.0  # real time: 5 s simulated = 5 s wall
    assert timeout(1.5) == pytest.approx(3.333, abs=1e-3)
    assert timeout(2) == 3.0  # the 3 s wall floor takes over
    assert timeout(10) == 3.0
    assert timeout(60) == 3.0  # 5 s simulated would be 83 ms: below any jitter


def make_watchdog(**kw) -> HeartbeatWatchdog:
    return HeartbeatWatchdog(timeout_s=kw.pop("timeout_s", 3.0), now=0.0, **kw)


def test_no_gap_while_the_heartbeat_keeps_changing() -> None:
    w = make_watchdog()
    ops = []
    for i in range(20):
        ops += w.on_heartbeat(T0 + timedelta(seconds=i), now=i * 0.5)
        ops += w.poll(now=i * 0.5 + 0.4)
    assert ops == []


def test_a_silent_heartbeat_opens_a_gap_at_the_last_heartbeat_and_closes_at_the_next() -> None:
    w = make_watchdog()
    w.on_heartbeat(T0, now=0.0)
    w.on_heartbeat(T0 + timedelta(seconds=1), now=0.1)
    assert w.poll(now=3.0) == []  # exactly the timeout: not yet
    assert w.poll(now=3.2) == [OpenGapOp(T0 + timedelta(seconds=1))]
    assert w.poll(now=4.0) == []  # only once
    ops = w.on_heartbeat(T0 + timedelta(seconds=241), now=4.5)
    assert ops == [CloseGapOp(T0 + timedelta(seconds=1), T0 + timedelta(seconds=241))]
    assert not w.in_gap


def test_a_replayed_or_older_heartbeat_is_not_a_sign_of_life() -> None:
    w = make_watchdog()
    w.on_heartbeat(T0, now=0.0)
    assert w.on_heartbeat(T0, now=2.9) == []  # same timestamp: a replay
    assert w.poll(now=3.2) != []  # so the silence still counts


def test_a_plc_restart_resetting_the_heartbeat_value_is_still_alive() -> None:
    """Liveness is the timestamp advancing, not the value (which restarts at 0)."""
    w = make_watchdog()
    w.on_heartbeat(T0, now=0.0)
    assert w.on_heartbeat(T0 + timedelta(seconds=1), now=1.0) == []
    assert w.poll(now=2.0) == []


def test_reconnecting_after_a_time_jump_records_the_blind_interval() -> None:
    w = make_watchdog()
    w.on_heartbeat(T0, now=0.0)
    w.on_connected(now=10.0)  # the session was lost and re-established within the timeout
    ops = w.on_heartbeat(T0 + timedelta(seconds=90), now=10.2)
    assert ops == [ClosedGapOp(T0, T0 + timedelta(seconds=90))]
    w.on_connected(now=20.0)  # a clean reconnection: simulated time did not move
    assert w.on_heartbeat(T0 + timedelta(seconds=91), now=20.2) == []


def test_collector_restart_compares_with_the_last_seen_time() -> None:
    down_for_a_minute = make_watchdog(last_seen_ts=T0)
    ops = down_for_a_minute.on_heartbeat(T0 + timedelta(seconds=60), now=1.0)
    assert ops == [ClosedGapOp(T0, T0 + timedelta(seconds=60))]

    quick_restart = make_watchdog(last_seen_ts=T0)
    assert quick_restart.on_heartbeat(T0 + timedelta(seconds=3), now=1.0) == []  # <= 5 s: no gap


def test_a_gap_open_in_the_database_is_closed_after_a_restart() -> None:
    w = make_watchdog(last_seen_ts=T0, open_gap_start=T0 - timedelta(seconds=30))
    assert w.in_gap
    assert w.poll(now=10.0) == []  # already open: not opened twice
    ops = w.on_heartbeat(T0 + timedelta(seconds=20), now=11.0)
    assert ops == [CloseGapOp(T0 - timedelta(seconds=30), T0 + timedelta(seconds=20))]


def test_nothing_is_declared_lost_before_the_first_heartbeat_ever_seen() -> None:
    w = make_watchdog()
    assert w.poll(now=100.0) == []  # a fresh database: no reference point yet


def test_first_run_records_the_time_before_the_first_observation_as_a_gap() -> None:
    w = make_watchdog()
    w.seed(T0)  # the oldest timestamp in the first subscription snapshot
    ops = w.on_heartbeat(T0 + timedelta(seconds=72), now=1.0)
    assert ops == [ClosedGapOp(T0, T0 + timedelta(seconds=72))]

    prompt = make_watchdog()
    prompt.seed(T0)
    assert (
        prompt.on_heartbeat(T0 + timedelta(seconds=2), now=1.0) == []
    )  # <= 5 s: nothing to report

    resumed = make_watchdog(last_seen_ts=T0)
    resumed.seed(T0 - timedelta(days=30))  # not a first run: seeding does nothing
    assert resumed.on_heartbeat(T0 + timedelta(seconds=1), now=1.0) == []
