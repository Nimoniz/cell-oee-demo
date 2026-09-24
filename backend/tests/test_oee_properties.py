"""Property-based tests: the OEE accounting must hold for *any* stops, gaps and counters."""

from __future__ import annotations

import math
from datetime import timedelta

from conftest import BASE, t
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.oee import (
    CounterSample,
    Gap,
    OeeParams,
    StopRecord,
    Window,
    check_invariant,
    combine,
    compute_oee_windows,
    counter_deltas,
)

START = t(5)
SETTINGS = settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])


def at(offset_s: float):
    return START + timedelta(seconds=offset_s)


@st.composite
def scenarios(draw):
    length = draw(st.integers(600, 16 * 3600))
    stops = [
        StopRecord(at(start), at(start + dur), draw(st.integers(2, 7)))
        for start, dur in draw(
            st.lists(
                st.tuples(st.integers(-3600, length + 3600), st.integers(1, 7200)), max_size=12
            )
        )
    ]
    if draw(st.booleans()):  # a stop still open at the live edge
        stops.append(StopRecord(at(draw(st.integers(0, length))), None, draw(st.integers(3, 7))))
    gaps = [
        Gap(at(start), at(start + dur) if dur else None)
        for start, dur in draw(
            st.lists(st.tuples(st.integers(-3600, length + 3600), st.integers(0, 3600)), max_size=4)
        )
    ]

    counter = draw(st.sampled_from([0, 100, 2**32 - 40]))
    scrap_counter = draw(st.sampled_from([0, 7, 2**32 - 10]))
    samples = []
    cursor = draw(st.integers(-7200, 0))
    for step in draw(
        st.lists(
            st.tuples(
                st.integers(1, 400),  # seconds since previous sample
                st.integers(0, 3),  # good parts
                st.integers(0, 1),  # scrap parts
                st.booleans() | st.just(False),  # PLC restart
                st.sampled_from([55.0, 55.0, 55.0, 60.0]),
            ),
            max_size=80,
        )
    ):
        seconds, good, scrap, reset, tct = step
        cursor += seconds
        counter = 0 if reset and draw(st.integers(0, 20)) == 0 else (counter + good) % 2**32
        scrap_counter = (scrap_counter + scrap) % 2**32
        samples.append(CounterSample(at(cursor), counter, scrap_counter, tct))

    cuts = sorted(draw(st.lists(st.integers(1, length - 1), max_size=4, unique=True)))
    return length, stops, gaps, samples, cuts


def partition(length: int, cuts: list[int]) -> list[Window]:
    bounds = [0, *cuts, length]
    return [
        Window(f"w{i}", at(a), at(b))
        for i, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False))
    ]


def close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


@SETTINGS
@given(scenarios())
def test_accounting_holds_for_every_window_and_every_grouping(scenario) -> None:
    length, stops, gaps, samples, cuts = scenario
    until = at(length)
    windows = partition(length, cuts)
    results = compute_oee_windows(windows, stops, samples, gaps, OeeParams(), until)
    for r in results:
        check_invariant(r)  # time identity, OEE == good x TCT / planned, OEE == A x P x Q
        assert min(r.gap_s, r.planned_stop_s, r.planned_s, r.operating_s, r.micro_s) >= 0
        assert min(r.fault_s, r.setup_s, r.starved_s, r.blocked_s, r.manual_s) >= 0
        assert r.good >= 0 and r.scrap >= 0 and r.dropped_parts >= 0  # never negative production
        assert r.loss_s <= r.planned_s + 1e-9
        if r.availability is not None:
            assert -1e-12 <= r.availability <= 1 + 1e-12
    check_invariant(combine(results))  # ...also for the grouping of all of them


@SETTINGS
@given(scenarios())
def test_splitting_a_window_changes_nothing(scenario) -> None:
    """Times and parts are additive, so the union of the parts equals the whole."""
    length, stops, gaps, samples, cuts = scenario
    until = at(length)
    whole = compute_oee_windows(partition(length, []), stops, samples, gaps, OeeParams(), until)[0]
    parts = combine(
        compute_oee_windows(partition(length, cuts), stops, samples, gaps, OeeParams(), until)
    )
    for field in (
        "gap_s",
        "opening_s",
        "planned_stop_s",
        "planned_s",
        "fault_s",
        "setup_s",
        "starved_s",
        "blocked_s",
        "manual_s",
        "micro_s",
        "operating_s",
        "good_tct_s",
        "total_tct_s",
    ):
        assert close(getattr(whole, field), getattr(parts, field)), field
    assert (whole.good, whole.scrap, whole.dropped_parts) == (
        parts.good,
        parts.scrap,
        parts.dropped_parts,
    )
    assert close(whole.oee, parts.oee)
    assert close(whole.availability, parts.availability)


@SETTINGS
@given(scenarios())
def test_every_part_is_either_counted_or_dropped(scenario) -> None:
    length, stops, gaps, samples, cuts = scenario
    (r,) = compute_oee_windows(partition(length, []), stops, samples, gaps, OeeParams(), at(length))
    window_parts = sum(
        d.parts for d in counter_deltas(samples) if r.window.start <= d.ts < r.window.end
    )
    assert r.total_parts + r.dropped_parts == window_parts


@SETTINGS
@given(scenarios())
def test_a_higher_micro_stop_threshold_never_lowers_availability(scenario) -> None:
    length, stops, gaps, samples, _ = scenario
    window = partition(length, [])
    operating = [
        compute_oee_windows(window, stops, samples, gaps, OeeParams(micro_stop_s=thr), at(length))[
            0
        ].operating_s
        for thr in (0.0, 120.0, 1e9)
    ]
    assert operating[0] <= operating[1] + 1e-9 <= operating[2] + 2e-9


def test_the_base_date_is_the_one_the_tests_assume() -> None:
    assert BASE.year == 2026 and BASE + timedelta(hours=5) == START
