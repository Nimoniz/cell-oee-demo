from __future__ import annotations

import pytest
from conftest import BLOCKED, FAULT, MANUAL, SETUP, STARVED, t, win

from app.oee import (
    CounterSample,
    Gap,
    ParetoBucket,
    StopFact,
    StopRecord,
    WeldPoint,
    bucket_start,
    choose_bucket_s,
    compute_oee_windows,
    compute_pareto,
    compute_weld_buckets,
    counter_deltas,
)

# ------------------------------------------------------------------ pareto


def fact(start, end, state, station="OP20", fault=0, cause=None) -> StopFact:
    return StopFact(start, end, state, station, fault, cause)


def bucket(result, key: str) -> ParetoBucket:
    return next(b for b in result.buckets if b.key == key)


def test_pareto_by_cause_groups_qualified_and_trails_unqualified() -> None:
    facts = [
        fact(t(6), t(6, 5), FAULT, fault=202, cause="electrical_breakdown"),
        fact(t(7), t(7, 5), FAULT, fault=202, cause="electrical_breakdown"),
        fact(t(8), t(8, 3), FAULT, fault=101, cause=None),
    ]
    r = compute_pareto(facts, win(t(5), t(13)), [], "cause", "duration")
    assert [b.key for b in r.buckets] == ["electrical_breakdown", "unqualified"]
    assert bucket(r, "electrical_breakdown").duration_s == 600
    assert bucket(r, "unqualified").duration_s == 180
    assert bucket(r, "unqualified").unqualified_duration_s == 180  # the whole bucket


def test_pareto_by_station_carries_a_hatched_unqualified_share() -> None:
    facts = [
        fact(t(6), t(6, 3), FAULT, station="OP10", cause="missing_parts"),
        fact(t(7), t(7, 3), FAULT, station="OP10", cause=None),
    ]
    r = compute_pareto(facts, win(t(5), t(13)), [], "station", "duration")
    op10 = bucket(r, "OP10")
    assert op10.duration_s == 360
    assert op10.unqualified_duration_s == 180


def test_pareto_by_fault_code_falls_back_to_state_when_there_is_none() -> None:
    facts = [
        fact(t(6), t(6, 3), FAULT, fault=202),
        fact(t(7), t(7, 3), SETUP, fault=0),
        fact(t(8), t(8, 3), STARVED, fault=0),
    ]
    r = compute_pareto(facts, win(t(5), t(13)), [], "fault_code", "duration")
    assert {b.key for b in r.buckets} == {"202", "state_4", "state_5"}


def test_pareto_planned_stops_are_excluded() -> None:
    facts = [fact(t(9), t(9, 20), 2)]  # PLANNED_STOP
    r = compute_pareto(facts, win(t(5), t(13)), [], "station", "duration")
    assert r.buckets == ()


def test_pareto_micro_stops_are_summarised_not_bucketed() -> None:
    facts = [fact(t(6), t(6, 1), FAULT, fault=101)]  # 60 s: a micro-stop
    r = compute_pareto(facts, win(t(5), t(13)), [], "station", "duration")
    assert r.buckets == ()
    assert (r.micro_count, r.micro_duration_s) == (1, 60)


def test_pareto_duration_metric_is_clipped_to_the_window_and_excludes_gaps() -> None:
    facts = [fact(t(4, 30), t(5, 30), FAULT, fault=202)]  # straddles the window start
    r = compute_pareto(facts, win(t(5), t(13)), [Gap(t(5, 10), t(5, 20))], "station", "duration")
    assert bucket(r, "OP20").duration_s == 30 * 60 - 10 * 60  # clipped at 05:00, minus the gap
    assert bucket(r, "OP20").count == 0  # it did not *start* inside the window


def test_pareto_count_metric_counts_arrivals_in_the_window() -> None:
    facts = [fact(t(6), t(6, 3), FAULT, fault=202), fact(t(14), t(14, 3), FAULT, fault=202)]
    r = compute_pareto(facts, win(t(5), t(13)), [], "station", "count")
    assert bucket(r, "OP20").count == 1


def test_pareto_open_stop_is_bounded_at_until() -> None:
    facts = [fact(t(6), None, BLOCKED, station="OP30")]
    r = compute_pareto(facts, win(t(5), t(13)), [], "station", "duration", until=t(6, 4))
    assert bucket(r, "OP30").duration_s == 240


def test_pareto_sums_to_the_same_loss_time_as_compute_oee() -> None:
    stops = [
        StopRecord(t(6), t(6, 10), FAULT),
        StopRecord(t(7), t(7, 5), SETUP),
        StopRecord(t(8), t(8, 4), STARVED),
        StopRecord(t(9, 30), t(9, 33), BLOCKED),
        StopRecord(t(11), t(11, 8), MANUAL),
        StopRecord(t(11, 30), t(11, 30, 40), FAULT),  # a micro-stop: excluded from both sides
    ]
    facts = [fact(s.start, s.end, s.state, fault=202 if s.state == FAULT else 0) for s in stops]
    window = win(t(5), t(13))
    (oee,) = compute_oee_windows([window], stops, [], [])
    pareto = compute_pareto(facts, window, [], "station", "duration")
    assert sum(b.duration_s for b in pareto.buckets) == pytest.approx(oee.loss_s)


# ------------------------------------------------------------------ weld buckets


def test_bucket_start_matches_a_hand_computed_time_bucket() -> None:
    origin = t(5)
    assert bucket_start(t(5, 7, 30), origin, 300) == t(5, 5)  # 5-min buckets, aligned on 05:00
    assert bucket_start(t(5, 10), origin, 300) == t(5, 10)  # exactly on a boundary
    assert bucket_start(t(4, 58), origin, 300) == t(4, 55)  # before the origin: still aligned


def test_choose_bucket_s_keeps_the_series_short_and_never_below_the_floor() -> None:
    assert choose_bucket_s(3600, target_buckets=300, floor_s=60) == 60
    assert choose_bucket_s(7 * 86_400, target_buckets=300) >= 60
    assert choose_bucket_s(7 * 86_400, target_buckets=300) * 300 >= 7 * 86_400


def test_weld_bucket_without_points_has_null_stats_not_zero() -> None:
    buckets = compute_weld_buckets([], [], [], t(5), t(5, 10), 300, t(5))
    assert len(buckets) == 2
    assert all(b.avg_ka is None and b.points == 0 for b in buckets)
    assert all(b.scrap_rate is None for b in buckets)


def test_weld_bucket_aggregates_current_and_nok_count() -> None:
    points = [
        WeldPoint(t(5, 1), 8.0, True),
        WeldPoint(t(5, 2), 7.0, False),
        WeldPoint(t(5, 6), 8.5, True),  # next bucket
    ]
    buckets = compute_weld_buckets(points, [], [], t(5), t(5, 10), 300, t(5))
    first, second = buckets
    assert (first.avg_ka, first.min_ka, first.max_ka) == (pytest.approx(7.5), 7.0, 8.0)
    assert (first.points, first.points_nok) == (2, 1)
    assert second.points == 1


def test_weld_bucket_scrap_rate_uses_counter_deltas_like_oee_does() -> None:
    samples = [
        CounterSample(t(5, 0), 0, 0, 55),
        CounterSample(t(5, 2), 10, 1, 55),
        CounterSample(t(5, 7), 20, 1, 55),
    ]
    deltas = counter_deltas(samples)
    buckets = compute_weld_buckets([], deltas, [], t(5), t(5, 10), 300, t(5))
    first, second = buckets
    assert (first.good, first.scrap) == (10, 1)
    assert first.scrap_rate == pytest.approx(1 / 11)
    assert (second.good, second.scrap) == (10, 0)


def test_weld_bucket_drops_parts_across_a_gap_exactly_like_compute_oee() -> None:
    samples = [
        CounterSample(t(5, 0), 0, 0, 55),
        CounterSample(t(5, 4), 10, 0, 55),  # interval crosses the gap: dropped
        CounterSample(t(5, 8), 20, 0, 55),
    ]
    deltas = counter_deltas(samples)
    gaps = [Gap(t(5, 1), t(5, 3))]
    buckets = compute_weld_buckets([], deltas, gaps, t(5), t(5, 10), 300, t(5))
    assert sum(b.good for b in buckets) == 10  # only the second delta is counted


def test_weld_bucket_alignment_is_independent_of_the_query_range() -> None:
    """Buckets are anchored on day_origin, not on `frm`: a later query sees the same grid."""
    points = [WeldPoint(t(5, 7), 8.0, True)]
    a = compute_weld_buckets(points, [], [], t(5), t(5, 10), 300, t(5))
    b = compute_weld_buckets(points, [], [], t(5, 3), t(5, 10), 300, t(5))
    assert a[1].start == b[1].start == t(5, 5)  # same grid: the later query just starts mid-bucket
