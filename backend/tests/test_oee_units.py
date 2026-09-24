from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest
from conftest import (
    BLOCKED,
    FAULT,
    MANUAL,
    PLAN,
    PLANNED_STOP,
    SETUP,
    STARVED,
    sample,
    t,
    win,
)

from app.oee import (
    DeltaKind,
    Gap,
    OeeInvariantError,
    OeeParams,
    StopKind,
    StopRecord,
    Window,
    check_invariant,
    classify_stops,
    combine,
    compute_oee_grouped,
    compute_oee_windows,
    counter_deltas,
    day_windows,
    shift_windows,
)
from app.oee import intervals as iv
from app.oee.types import DEFAULT_PARAMS

# ------------------------------------------------------------------ intervals


def test_interval_algebra() -> None:
    assert iv.merge([(5, 7), (1, 3), (2, 4), (7, 8), (9, 9)]) == [(1, 4), (5, 8)]
    assert iv.clip([(0, 10), (20, 30)], 5, 25) == [(5, 10), (20, 25)]
    assert iv.complement([(2, 4), (6, 7)], 0, 10) == [(0, 2), (4, 6), (7, 10)]
    assert iv.intersect([(0, 5), (8, 12)], [(3, 9)]) == [(3, 5), (8, 9)]
    assert iv.subtract([(0, 10)], [(2, 3), (5, 6), (9, 12)]) == [(0, 2), (3, 5), (6, 9)]
    assert iv.total([(0, 2), (4, 5)]) == 3


# ------------------------------------------------------------------ counters


def test_first_sample_has_no_baseline() -> None:
    (d,) = counter_deltas([sample(t(5), 1000, 50)])
    assert (d.good, d.scrap, d.kind) == (0, 0, DeltaKind.FIRST)


def test_normal_deltas_are_attributed_to_the_later_sample() -> None:
    _, b, c = counter_deltas([sample(t(5), 10), sample(t(5, 1), 11), sample(t(5, 2), 11, 1)])
    assert (b.good, b.scrap, b.ts, b.prev_ts) == (1, 0, t(5, 1), t(5))
    assert (c.good, c.scrap) == (0, 1)


def test_unsorted_samples_are_ordered_by_source_timestamp() -> None:
    deltas = counter_deltas([sample(t(5, 2), 12), sample(t(5), 10), sample(t(5, 1), 11)])
    assert [d.ts for d in deltas] == [t(5), t(5, 1), t(5, 2)]
    assert [d.good for d in deltas] == [0, 1, 1]


def test_uint32_rollover_is_a_wrap_not_a_reset() -> None:
    _, d = counter_deltas([sample(t(5), 2**32 - 3, 2**32 - 1), sample(t(5, 1), 2, 0)])
    assert (d.good, d.scrap, d.kind) == (5, 1, DeltaKind.ROLLOVER)


def test_counter_reset_produces_nothing_and_rebases() -> None:
    _, b, c = counter_deltas([sample(t(5), 500, 20), sample(t(5, 1), 0, 0), sample(t(5, 2), 1, 0)])
    assert (b.good, b.scrap, b.kind) == (0, 0, DeltaKind.RESET)
    assert (c.good, c.kind) == (1, DeltaKind.NORMAL)  # counting again from the new baseline


def test_reset_to_a_large_value_is_not_mistaken_for_a_rollover() -> None:
    _, d = counter_deltas([sample(t(5), 5000), sample(t(5, 1), 4000)])
    assert d.kind is DeltaKind.RESET and d.good == 0


def test_a_decrease_far_from_the_wrap_point_is_a_reset_even_if_the_new_value_is_small() -> None:
    _, d = counter_deltas([sample(t(5), 2**31), sample(t(5, 1), 3)])
    assert d.kind is DeltaKind.RESET


# ------------------------------------------------------------------ stop classification


@pytest.mark.parametrize(
    ("seconds", "kind"),
    [(119.9, StopKind.MICRO), (120.0, StopKind.LOSS), (120.1, StopKind.LOSS), (1, StopKind.MICRO)],
)
def test_micro_stop_threshold(seconds: float, kind: StopKind) -> None:
    (c,) = classify_stops([StopRecord(t(6), t(6, 0, seconds), FAULT)], t(7))
    assert c.kind is kind


def test_micro_stop_threshold_is_configurable() -> None:
    stop = StopRecord(t(6), t(6, 3), FAULT)  # 180 s
    assert classify_stops([stop], t(7), 120)[0].kind is StopKind.LOSS
    assert classify_stops([stop], t(7), 300)[0].kind is StopKind.MICRO


def test_planned_stops_are_never_micro() -> None:
    (c,) = classify_stops([StopRecord(t(9), t(9, 0, 30), PLANNED_STOP)], t(10))
    assert c.kind is StopKind.PLANNED


def test_open_stop_lasts_until_now_and_becomes_a_loss_retroactively() -> None:
    stop = StopRecord(t(6), None, STARVED)
    assert classify_stops([stop], t(6, 1, 30))[0].kind is StopKind.MICRO  # 90 s so far
    late = classify_stops([stop], t(6, 2, 30))[0]  # 150 s
    assert late.kind is StopKind.LOSS and late.end == t(6, 2, 30) and late.is_induced


def test_producing_is_not_a_stop() -> None:
    with pytest.raises(ValueError):
        classify_stops([StopRecord(t(6), t(7), 1)], t(8))


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError):
        StopRecord(datetime(2026, 1, 5, 6), None, FAULT)
    with pytest.raises(ValueError):
        Window("w", datetime(2026, 1, 5, 6), t(7))


# ------------------------------------------------------------------ windows


def test_shift_windows_over_a_production_day() -> None:
    ws = shift_windows(t(5), t(5, day=1), PLAN)
    assert [(w.label, w.start, w.end) for w in ws] == [
        ("2026-01-05 matin", t(5), t(13)),
        ("2026-01-05 apres-midi", t(13), t(21)),
        ("2026-01-05 nuit", t(21), t(5, day=1)),
    ]


def test_shift_windows_are_clipped_and_the_night_belongs_to_its_start_date() -> None:
    ws = shift_windows(t(0), t(8), PLAN)
    assert [(w.label, w.start, w.end) for w in ws] == [
        ("2026-01-04 nuit", t(0), t(5)),
        ("2026-01-05 matin", t(5), t(8)),
    ]


def test_production_day_runs_from_five_to_five() -> None:
    ws = day_windows(t(0), t(12, day=1), PLAN)
    assert [(w.label, w.start, w.end) for w in ws] == [
        ("2026-01-04", t(0), t(5)),
        ("2026-01-05", t(5), t(5, day=1)),
        ("2026-01-06", t(5, day=1), t(12, day=1)),
    ]


def test_empty_range_has_no_windows() -> None:
    assert shift_windows(t(6), t(6), PLAN) == []
    assert day_windows(t(6), t(6), PLAN) == []


# ------------------------------------------------------------------ OEE, hand-computed


def one(window: Window, stops=(), samples=(), gaps=(), params=DEFAULT_PARAMS, until=None):
    (result,) = compute_oee_windows([window], list(stops), list(samples), list(gaps), params, until)
    check_invariant(result)
    return result


def test_hand_computed_shift() -> None:
    """05:00-13:00 (28 800 s): 20 min break, 30 min fault, 5 min starved, one 60 s micro-stop."""
    stops = [
        StopRecord(t(9), t(9, 20), PLANNED_STOP),
        StopRecord(t(7), t(7, 30), FAULT),
        StopRecord(t(8), t(8, 5), STARVED),
        StopRecord(t(10), t(10, 1), FAULT),  # 60 s: micro
    ]
    samples = [sample(t(5), 0, 0), sample(t(12, 59), 390, 10)]
    r = one(win(t(5), t(13)), stops, samples)
    assert r.opening_s == 28_800
    assert r.planned_stop_s == 1_200 and r.planned_s == 27_600
    assert (r.fault_s, r.starved_s, r.micro_s) == (1_800, 300, 60)
    assert r.induced_s == 300
    assert r.operating_s == 27_600 - 2_100 == 25_500  # the micro-stop stays inside
    assert r.total_parts == 400
    assert r.availability == pytest.approx(25_500 / 27_600)
    assert r.performance == pytest.approx(400 * 55 / 25_500)
    assert r.quality == pytest.approx(390 / 400)
    assert r.oee == pytest.approx(390 * 55 / 27_600)  # reference invariant


def test_every_loss_category_is_reported_separately() -> None:
    stops = [
        StopRecord(t(6), t(6, 10), FAULT),
        StopRecord(t(7), t(7, 5), SETUP),
        StopRecord(t(8), t(8, 4), STARVED),
        StopRecord(t(9, 30), t(9, 33), BLOCKED),
        StopRecord(t(11), t(11, 8), MANUAL),
    ]
    r = one(win(t(5), t(13)), stops)
    assert (r.fault_s, r.setup_s, r.starved_s, r.blocked_s, r.manual_s) == (600, 300, 240, 180, 480)


# ------------------------------------------------------------------ stops across shifts


def test_stop_across_two_shifts_is_split_pro_rata() -> None:
    stops = [StopRecord(t(12, 30), t(13, 15), FAULT)]  # 45 min over the 13:00 boundary
    morning, afternoon = compute_oee_windows(
        shift_windows(t(5), t(21), PLAN), stops, [], [], OeeParams(), t(21)
    )
    assert (morning.fault_s, afternoon.fault_s) == (1_800, 900)
    assert morning.fault_s + afternoon.fault_s == 2_700
    check_invariant(morning)
    check_invariant(afternoon)


def test_micro_classification_uses_the_whole_stop_not_the_part_in_the_shift() -> None:
    stops = [StopRecord(t(12, 59, 0), t(13, 1, 10), FAULT)]  # 130 s: a stop, cut 60 s / 70 s
    morning, afternoon = compute_oee_windows(
        shift_windows(t(5), t(21), PLAN), stops, [], [], OeeParams(), t(21)
    )
    assert (morning.fault_s, afternoon.fault_s) == (60, 70)  # both losses, though each < 120 s
    assert morning.micro_s == afternoon.micro_s == 0


def test_a_100_s_stop_is_micro_wherever_it_falls() -> None:
    stops = [StopRecord(t(12, 59, 20), t(13, 1, 0), FAULT)]  # 100 s cut 40 s / 60 s
    morning, afternoon = compute_oee_windows(
        shift_windows(t(5), t(21), PLAN), stops, [], [], OeeParams(), t(21)
    )
    assert morning.fault_s == afternoon.fault_s == 0
    assert (morning.micro_s, afternoon.micro_s) == (40, 60)


def test_stop_spanning_the_production_day_boundary() -> None:
    stops = [StopRecord(t(4, 40, day=1), t(5, 20, day=1), FAULT)]  # 05:00 = day and shift boundary
    windows = day_windows(t(5), t(5, day=2), PLAN)
    days = compute_oee_windows(windows, stops, [], [], OeeParams(), t(5, day=2))
    assert [d.fault_s for d in days] == [1_200, 1_200]


# ------------------------------------------------------------------ empty periods, zero, null


def test_period_without_any_planned_time_is_null_not_zero() -> None:
    zero_length = one(win(t(6), t(6)))
    assert (zero_length.availability, zero_length.performance, zero_length.quality) == (None,) * 3
    assert zero_length.oee is None

    all_break = one(win(t(9), t(9, 20)), [StopRecord(t(9), t(9, 20), PLANNED_STOP)])
    assert all_break.planned_s == 0 and all_break.oee is None

    all_gap = one(win(t(6), t(7)), gaps=[Gap(t(5, 30), t(7, 30))])
    assert all_gap.opening_s == 0 and all_gap.gap_s == 3_600 and all_gap.oee is None


def test_time_with_no_good_parts_is_zero_not_null() -> None:
    r = one(win(t(6), t(7)), samples=[sample(t(6), 100), sample(t(6, 30), 100)])
    assert r.oee == 0.0 and r.availability == 1.0 and r.performance == 0.0 and r.quality is None


def test_fully_stopped_period_has_zero_availability_and_undefined_performance() -> None:
    r = one(win(t(6), t(7)), [StopRecord(t(5, 50), t(7, 10), FAULT)])
    assert r.availability == 0.0 and r.performance is None and r.quality is None
    assert r.oee == 0.0


def test_parts_without_operating_time_are_flagged() -> None:
    r = one(
        win(t(6), t(7)),
        [StopRecord(t(5, 50), t(7, 10), FAULT)],
        [sample(t(6), 0), sample(t(6, 30), 5)],
    )
    assert r.anomalies == ("parts_without_operating_time",)
    assert r.oee == pytest.approx(5 * 55 / 3_600)  # still the reference quantity


# ------------------------------------------------------------------ communication gaps


def test_gaps_are_excluded_from_opening_time_and_from_stops() -> None:
    stops = [StopRecord(t(6), t(7), FAULT)]  # 1 h fault, 20 min of it blind
    r = one(win(t(5), t(9)), stops, gaps=[Gap(t(6, 20), t(6, 40))])
    assert r.gap_s == 1_200 and r.opening_s == 4 * 3_600 - 1_200
    assert r.fault_s == 2_400
    assert r.planned_s == r.opening_s
    assert r.operating_s == r.planned_s - 2_400


def test_parts_produced_during_a_gap_are_dropped_with_the_time() -> None:
    samples = [
        sample(t(6, 0), 0),
        sample(t(6, 10), 10),  # observed
        sample(t(6, 50), 30),  # interval 6:10-6:50 contains the gap: 20 parts unobservable
        sample(t(7, 0), 40),  # observed again
    ]
    r = one(win(t(6), t(7, 30)), samples=samples, gaps=[Gap(t(6, 20), t(6, 40))])
    assert (r.good, r.dropped_parts) == (20, 20)  # 10 before + 10 after; 20 dropped


def test_open_gap_runs_to_the_end_of_the_window_and_drops_later_parts() -> None:
    samples = [sample(t(6, 0), 0), sample(t(6, 10), 10), sample(t(6, 50), 30)]
    r = one(win(t(6), t(7)), samples=samples, gaps=[Gap(t(6, 20), None)])
    assert r.gap_s == 2_400 and r.opening_s == 1_200
    assert (r.good, r.dropped_parts) == (10, 20)


def test_gap_before_the_window_still_drops_the_part_that_spans_it() -> None:
    samples = [sample(t(4, 0), 0), sample(t(5, 1), 12)]  # lead-in sample, then first in-window one
    r = one(win(t(5), t(6)), samples=samples, gaps=[Gap(t(4, 10), t(4, 50))])
    assert (r.good, r.dropped_parts) == (0, 12)
    assert r.gap_s == 0


def test_counters_across_rollover_and_reset_feed_the_oee() -> None:
    samples = [
        sample(t(6, 0), 2**32 - 2, 0),
        sample(t(6, 10), 3, 0),  # rollover: +5
        sample(t(6, 20), 8, 0),  # +5
        sample(t(6, 30), 0, 0),  # PLC restart: nothing invented
        sample(t(6, 40), 4, 0),  # +4
    ]
    r = one(win(t(6), t(7)), samples=samples)
    assert r.good == 14 and r.dropped_parts == 0


def test_theoretical_cycle_time_change_is_honoured_per_delta() -> None:
    samples = [sample(t(6), 0, 0, 55), sample(t(6, 10), 10, 0, 55), sample(t(6, 20), 20, 0, 60)]
    r = one(win(t(6), t(7)), samples=samples)
    assert r.good_tct_s == 10 * 55 + 10 * 60


# ------------------------------------------------------------------ open stop at the live edge


def test_live_oee_open_stop_jumps_when_it_crosses_the_micro_threshold() -> None:
    stops = [StopRecord(t(6), None, FAULT)]
    window = win(t(5), t(6, 5))
    before = one(window, stops, until=t(6, 1, 30))  # 90 s so far: micro
    after = one(window, stops, until=t(6, 2, 30))  # 150 s: a stop since 06:00:00, no smoothing
    assert before.fault_s == 0 and before.micro_s == 90
    assert after.fault_s == 150 and after.micro_s == 0


# ------------------------------------------------------------------ combining and grouping


def test_combined_oee_is_the_oee_of_the_union_not_the_mean_of_ratios() -> None:
    stops = [
        StopRecord(t(13, 0), t(15, 0), FAULT),
        StopRecord(t(16, 0), t(20, 0), PLANNED_STOP),  # the two shifts have different weights
    ]
    samples = [sample(t(5), 0), sample(t(12, 59), 500), sample(t(20, 59), 620, 5)]
    windows = shift_windows(t(5), t(21), PLAN)
    shifts = compute_oee_windows(windows, stops, samples, [], OeeParams(), t(21))
    whole = one(win(t(5), t(21)), stops, samples)
    total = combine(shifts)
    check_invariant(total)
    assert total.oee == pytest.approx(whole.oee)
    assert total.availability == pytest.approx(whole.availability)
    naive_mean = sum(s.oee for s in shifts) / len(shifts)
    assert total.oee != pytest.approx(naive_mean)


def test_grouped_by_shift_and_by_day_agree() -> None:
    stops = [StopRecord(t(9), t(9, 20), PLANNED_STOP), StopRecord(t(22), t(22, 30), FAULT)]
    samples = [sample(t(5), 0), sample(t(12), 300), sample(t(20), 700), sample(t(4, day=1), 1000)]
    frm, to = t(5), t(5, day=1)
    by_shift = compute_oee_grouped(frm, to, "shift", PLAN, stops, samples, [], OeeParams(), to)
    (by_day,) = compute_oee_grouped(frm, to, "day", PLAN, stops, samples, [], OeeParams(), to)
    assert len(by_shift) == 3
    assert combine(by_shift).oee == pytest.approx(by_day.oee)
    assert combine(by_shift).planned_s == pytest.approx(by_day.planned_s)


def test_combine_rejects_nothing() -> None:
    with pytest.raises(ValueError):
        combine([])


# ------------------------------------------------------------------ the invariant checker itself


def test_check_invariant_catches_a_wrong_oee() -> None:
    good = one(win(t(6), t(7)), samples=[sample(t(6), 0), sample(t(6, 30), 30)])
    check_invariant(good)
    with pytest.raises(OeeInvariantError):
        check_invariant(replace(good, oee=good.oee + 0.01))
    with pytest.raises(OeeInvariantError):
        check_invariant(replace(good, operating_s=good.operating_s - 10))


def test_overlapping_stops_never_double_count_time() -> None:
    stops = [
        StopRecord(t(6), t(6, 30), FAULT),
        StopRecord(t(6, 20), t(6, 50), SETUP),  # overlaps the fault (bad data): claimed once
        StopRecord(t(6, 10), t(6, 40), PLANNED_STOP),
    ]
    r = one(win(t(6), t(7)), stops)
    assert r.planned_stop_s == 1_800
    assert r.fault_s + r.setup_s + r.planned_stop_s <= 3_600


def test_quality_is_cycle_time_weighted_so_the_invariant_survives_a_tct_change() -> None:
    """Found by hypothesis: a plain good/total quality breaks OEE = A x P x Q if TCT changes."""
    samples = [
        sample(t(6, 0, 1), 0, 0, 55),
        sample(t(6, 0, 2), 0, 1, 55),
        sample(t(6, 0, 3), 1, 1, 60),
    ]
    r = one(win(t(6), t(6, 10)), samples=samples)
    assert r.quality == pytest.approx(
        60 / 115
    )  # weighted; equals good / total when TCT is constant
    assert r.oee == pytest.approx(60 / 600)
