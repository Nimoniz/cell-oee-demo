from __future__ import annotations

from conftest import t, win

from app.oee import StateEvent, build_timeline


def test_timeline_segments_span_until_the_next_event() -> None:
    events = [StateEvent(t(6), 1), StateEvent(t(7), 3), StateEvent(t(8), 1)]
    segs = build_timeline(events, win(t(5), t(13)), until=t(9))
    assert [(s.start, s.end, s.state) for s in segs] == [
        (t(6), t(7), 1),
        (t(7), t(8), 3),
        (t(8), t(9), 1),
    ]


def test_lead_in_event_before_the_window_is_clipped_to_window_start() -> None:
    events = [StateEvent(t(4), 1), StateEvent(t(6), 3)]
    segs = build_timeline(events, win(t(5), t(13)), until=t(7))
    assert (segs[0].start, segs[0].end, segs[0].state) == (t(5), t(6), 1)
    assert (segs[1].start, segs[1].end, segs[1].state) == (t(6), t(7), 3)


def test_open_ended_last_segment_runs_to_until() -> None:
    events = [StateEvent(t(6), 3)]
    segs = build_timeline(events, win(t(5), t(13)), until=t(6, 5))
    assert segs == [type(segs[0])(t(6), t(6, 5), 3)]


def test_no_events_gives_an_empty_timeline() -> None:
    assert build_timeline([], win(t(5), t(13)), until=t(6)) == []


def test_events_after_until_are_ignored() -> None:
    events = [StateEvent(t(6), 1), StateEvent(t(10), 3)]
    segs = build_timeline(events, win(t(5), t(13)), until=t(8))
    assert [(s.start, s.end, s.state) for s in segs] == [(t(6), t(8), 1)]
