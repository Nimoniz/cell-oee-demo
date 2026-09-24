from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from conftest import CONFIG_PATH, DAY, HOUR, make_config
from pydantic import ValidationError

from cell_sim.clock import SimClock
from cell_sim.config import load_config
from cell_sim.rng import RngFactory
from cell_sim.shifts import ShiftCalendar

START = dt.datetime(2026, 1, 5, 5, 0, tzinfo=dt.UTC).timestamp()


def at(hh: int, mm: int = 0, day: int = 0) -> float:
    return START + day * DAY + (hh - 5) * HOUR + mm * 60


# ------------------------------------------------------------------ config


def test_shipped_config_loads() -> None:
    cfg = load_config(CONFIG_PATH)
    assert cfg.clock.start_ts == START
    assert cfg.cell.theoretical_cycle_s == 55
    assert cfg.weld.points_per_part == 12
    assert 201 not in cfg.faults


@pytest.mark.parametrize(
    "overrides",
    [
        {"faults": {201: {"mtbf_min": 100}}},  # wear-driven, no MTBF
        {"faults": {999: {"mtbf_min": 100}}},
        {"clock": {"acceleration": 100}},
        {"clock": {"acceleration": 0.5}},
        {"shifts": [{"name": "a", "start": "05:00", "end": "13:00"}]},  # not contiguous
        {"shifts": [{"name": "a", "start": "5h", "end": "5h"}]},
        {"manual": {"after_faults": [12345]}},
        {"cell": {"unknown_key": 1}},
    ],
)
def test_invalid_config_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_config(**overrides)


# ------------------------------------------------------------------ clock


def test_clock_applies_acceleration() -> None:
    wall = [100.0]
    clock = SimClock(start_ts=1000.0, acceleration=30, wall=lambda: wall[0])
    assert clock.now() == 1000.0
    wall[0] = 102.0
    assert clock.now() == 1060.0
    assert clock.wall_delay(1120.0) == pytest.approx(2.0)  # 60 sim-s ahead = 2 wall-s
    assert clock.wall_delay(1000.0) == 0.0  # already past: no sleep, no catch-up delay


# ------------------------------------------------------------------ rng


def test_rng_streams_reproducible_and_independent() -> None:
    a, b = RngFactory(1), RngFactory(1)
    assert [a.stream("x").random() for _ in range(3)] == [b.stream("x").random() for _ in range(3)]
    assert RngFactory(1).stream("x").random() != RngFactory(1).stream("y").random()
    assert RngFactory(1).stream("x").random() != RngFactory(2).stream("x").random()
    assert RngFactory(1, "a").stream("x").random() != RngFactory(1, "b").stream("x").random()


# ------------------------------------------------------------------ shifts


@pytest.fixture
def calendar() -> ShiftCalendar:
    cfg = make_config()
    return ShiftCalendar(cfg.shifts, cfg.planned_break)


@pytest.mark.parametrize(
    ("hh", "mm", "name"),
    [
        (5, 0, "matin"),
        (12, 59, "matin"),
        (13, 0, "apres-midi"),
        (20, 59, "apres-midi"),
        (21, 0, "nuit"),
        (23, 30, "nuit"),
        (0, 0, "nuit"),
        (4, 59, "nuit"),
    ],
)
def test_shift_at(calendar: ShiftCalendar, hh: int, mm: int, name: str) -> None:
    day = 1 if hh < 5 else 0  # 00:00-04:59 belongs to the night shift started the day before
    assert calendar.shift_at(at(hh, mm, day)).name == name


def test_break_windows(calendar: ShiftCalendar) -> None:
    w = calendar.next_window(START)
    assert (w.start, w.end) == (at(9), at(9, 20))  # 4 h after the 05:00 shift start, 20 min
    assert calendar.next_window(at(9, 10)) == w  # in progress: still the current one
    assert calendar.next_window(at(9, 20)).start == at(17)
    assert calendar.next_window(at(17, 20)).start == at(1, 0, day=1)  # night shift: 21:00 + 4 h
    assert calendar.next_window(at(1, 20, day=1)).start == at(9, 0, day=1)
