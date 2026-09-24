"""API routes against a real TimescaleDB: REST, PATCH idempotence/conflicts, and the live WS."""

from __future__ import annotations

import time

import pytest
from conftest import t
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.app import create_app
from app.collector.store import CollectorStore
from app.collector.types import OpenStopOp, StateEventOp
from app.config import load_settings

pytestmark = pytest.mark.db


def settings_for(db_url: str):
    return load_settings().model_copy(update={"database_url": db_url})


def seed(sync_engine, sql: str, **params) -> None:
    with sync_engine.begin() as conn:
        conn.execute(text(sql), params)


def seed_shift_of_data(sync_engine) -> None:
    """A representative morning shift: a break, a qualified fault, an unqualified one, parts."""
    with sync_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO state_events (scope, ts, state, stop_origin) VALUES ('CELL', :t, 1, 0)"
            ),
            {"t": t(5)},
        )
        for start, end, state, origin, fault, cause in [
            (t(9), t(9, 20), 2, "CELL", 0, None),
            (t(7), t(7, 5), 3, "OP20", 202, "electrical_breakdown"),
            (t(8), t(8, 3), 3, "OP10", 101, None),
        ]:
            conn.execute(
                text(
                    "INSERT INTO stops (start_ts, end_ts, duration_s, state, origin_station, "
                    "fault_code, qualified_cause, qualified_at) "
                    "VALUES (:a, :b, :d, :st, :o, :f, :c, :qa)"
                ),
                {
                    "a": start,
                    "b": end,
                    "d": (end - start).total_seconds(),
                    "st": state,
                    "o": origin,
                    "f": fault,
                    "c": cause,
                    "qa": end if cause else None,
                },
            )
        for i, (good, scrap) in enumerate([(0, 0), (50, 1), (100, 2)]):
            conn.execute(
                text("INSERT INTO counter_samples VALUES (:t, :g, :s, 55, 56)"),
                {"t": t(5, 10 * i), "g": good, "s": scrap},
            )
        conn.execute(
            text("INSERT INTO collector_state (last_seen_ts) VALUES (:t)"), {"t": t(12, 59)}
        )


@pytest.fixture
def client(migrated_db_url: str, sync_engine):
    app = create_app(settings_for(migrated_db_url))
    with TestClient(app) as c:
        yield c


# ------------------------------------------------------------------ /api/oee


def test_oee_route_groups_and_totals(client, sync_engine) -> None:
    seed_shift_of_data(sync_engine)
    r = client.get("/api/oee", params={"from": t(5).isoformat(), "to": t(13).isoformat()})
    assert r.status_code == 200
    (whole,) = r.json()
    assert whole["good"] == 100 and whole["scrap"] == 2
    assert whole["oee"] == pytest.approx(100 * 55 / whole["planned_s"])

    r = client.get(
        "/api/oee",
        params={"from": t(5).isoformat(), "to": t(13).isoformat(), "group_by": "shift"},
    )
    assert len(r.json()) == 1
    assert r.json()[0]["label"] == "2026-01-05 matin"


def test_oee_route_rejects_naive_datetimes(client) -> None:
    r = client.get("/api/oee", params={"from": "2026-01-05T05:00:00", "to": "2026-01-05T13:00:00"})
    assert r.status_code == 422
    assert "UTC offset" in r.text


def test_oee_route_null_on_empty_range(client) -> None:
    r = client.get("/api/oee", params={"from": t(5).isoformat(), "to": t(5).isoformat()})
    (result,) = r.json()
    assert result["oee"] is None and result["availability"] is None


# ------------------------------------------------------------------ /api/stops and PATCH


def test_stops_route_lists_only_qualifiable_ones(client, sync_engine) -> None:
    seed_shift_of_data(sync_engine)
    r = client.get("/api/stops")
    assert r.status_code == 200
    stops = r.json()
    assert {s["fault_code"] for s in stops} == {101}  # the qualified 202 is excluded by default
    assert all(s["state"] != 2 for s in stops)  # no planned stop
    assert all(s["duration_s"] >= 120 for s in stops)


def test_stops_route_includes_the_open_stop_once_it_qualifies(client, sync_engine) -> None:
    seed(
        sync_engine,
        "INSERT INTO state_events (scope, ts, state, stop_origin) VALUES ('CELL', :t, 1, 0)",
        t=t(5),
    )
    seed(
        sync_engine,
        "INSERT INTO stops (start_ts, state, origin_station, fault_code) VALUES (:t, 3, 'OP20', 202)",
        t=t(6),
    )
    seed(sync_engine, "INSERT INTO collector_state (last_seen_ts) VALUES (:t)", t=t(6, 1, 30))
    r = client.get("/api/stops")
    assert r.json() == []  # only 90 s so far
    seed(sync_engine, "UPDATE collector_state SET last_seen_ts = :t", t=t(6, 2, 30))
    r = client.get("/api/stops")
    (stop,) = r.json()
    assert stop["open"] is True and stop["duration_s"] == pytest.approx(150)


def qualify(client, stop_id: int, cause: str):
    return client.patch(f"/api/stops/{stop_id}", json={"cause": cause})


def test_qualify_a_stop_and_idempotence(client, sync_engine) -> None:
    seed_shift_of_data(sync_engine)
    stop_id = client.get("/api/stops").json()[0]["id"]
    r = qualify(client, stop_id, "missing_parts")
    assert r.status_code == 200 and r.json()["qualified_cause"] == "missing_parts"
    assert client.get("/api/stops").json() == []  # now qualified: no longer listed by default
    again = qualify(client, stop_id, "other")  # re-qualifying is allowed and idempotent in shape
    assert again.status_code == 200 and again.json()["qualified_cause"] == "other"


def test_qualify_rejects_unknown_stop_cause_planned_and_micro(client, sync_engine) -> None:
    seed_shift_of_data(sync_engine)
    assert qualify(client, 999_999, "other").status_code == 404

    # Planned stops never appear in /api/stops (by design), so fetch their id straight from the DB.
    with sync_engine.connect() as conn:
        planned_id = conn.execute(text("SELECT id FROM stops WHERE state = 2")).scalar_one()
    assert qualify(client, planned_id, "other").status_code == 409

    fault = next(
        s
        for s in client.get("/api/stops", params={"unqualified": False}).json()
        if s["fault_code"] == 101
    )
    assert qualify(client, fault["id"], "not_a_real_cause").status_code == 422

    seed(
        sync_engine,
        "INSERT INTO stops (start_ts, end_ts, duration_s, state, origin_station, fault_code) "
        "VALUES (:a, :b, :d, 3, 'OP10', 103)",
        a=t(10),
        b=t(10, 0, 30),
        d=30.0,
    )
    with sync_engine.connect() as conn:
        micro_id = conn.execute(text("SELECT id FROM stops WHERE duration_s = 30")).scalar_one()
    assert qualify(client, micro_id, "other").status_code == 409


# ------------------------------------------------------------------ /api/stops/pareto


def test_pareto_route_dimensions_and_metrics(client, sync_engine) -> None:
    seed_shift_of_data(sync_engine)
    for by in ("cause", "station", "fault_code"):
        for metric in ("duration", "count"):
            r = client.get(
                "/api/stops/pareto",
                params={
                    "from": t(5).isoformat(),
                    "to": t(13).isoformat(),
                    "by": by,
                    "metric": metric,
                },
            )
            assert r.status_code == 200, (by, metric, r.text)
            assert r.json()["by"] == by and r.json()["metric"] == metric

    r = client.get(
        "/api/stops/pareto",
        params={"from": t(5).isoformat(), "to": t(13).isoformat(), "by": "cause"},
    )
    buckets = {b["key"]: b for b in r.json()["buckets"]}
    assert "unqualified" in buckets and buckets["unqualified"]["duration_s"] == pytest.approx(180)
    assert list(buckets)[-1] == "unqualified"  # the dedicated bar trails


# ------------------------------------------------------------------ /api/weld


def test_weld_route_returns_aligned_buckets_and_config(client, sync_engine) -> None:
    with sync_engine.begin() as conn:
        conn.execute(text("INSERT INTO weld_samples VALUES (:t, 8.1, 12, true)"), {"t": t(5, 7)})
        conn.execute(text("INSERT INTO weld_samples VALUES (:t, 6.9, 200, false)"), {"t": t(5, 8)})
    r = client.get("/api/weld", params={"from": t(5).isoformat(), "to": t(5, 10).isoformat()})
    assert r.status_code == 200
    body = r.json()
    assert body["nominal_ka"] == pytest.approx(8.0)
    assert body["tolerance_pct"] == pytest.approx(10.0)
    assert any(b["points"] > 0 for b in body["buckets"])
    assert body["buckets"][0]["start"].endswith("Z")


# ------------------------------------------------------------------ live: REST snapshot and WS


def test_live_route_reflects_the_database(client, sync_engine) -> None:
    seed_shift_of_data(sync_engine)
    r = client.get("/api/cell/live")
    assert r.status_code == 200
    body = r.json()
    assert body["good_total"] == 100 and body["scrap_total"] == 2
    assert body["comm_lost"] is False


def test_live_websocket_streams_an_initial_snapshot(client, sync_engine) -> None:
    seed_shift_of_data(sync_engine)
    with client.websocket_connect("/ws/live") as ws:
        # A client that connects right away may briefly see a snapshot cached before the seed
        # data was committed; the periodic (250 ms) refresh catches up shortly after.
        deadline = time.monotonic() + 5
        msg = ws.receive_json()
        while msg["now"] is None and time.monotonic() < deadline:
            msg = ws.receive_json()
        assert msg["now"] is not None
        assert msg["state"] == 1


def test_live_websocket_receives_a_state_change_pushed_by_the_collector(
    migrated_db_url: str, sync_engine
) -> None:
    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine

    settings = settings_for(migrated_db_url)
    seed_shift_of_data(sync_engine)
    app = create_app(settings)
    with TestClient(app) as client, client.websocket_connect("/ws/live") as ws:
        first = ws.receive_json()
        assert first["state"] == 1

        async def emit() -> None:
            engine = create_async_engine(migrated_db_url)
            try:
                store = CollectorStore(engine, notify_channel=settings.notify_channel)
                await store.apply(
                    [
                        StateEventOp("CELL", t(13, 5), 3, 202, 20),
                        OpenStopOp(t(13, 5), 3, "OP20", 202, None),
                    ],
                    t(13, 5),
                )
            finally:
                await engine.dispose()

        asyncio.run(emit())

        deadline = time.monotonic() + 10
        seen_fault = False
        while time.monotonic() < deadline:
            msg = ws.receive_json()
            if msg["state"] == 3 and msg["fault_code"] == 202:
                seen_fault = True
                break
        assert seen_fault
        assert msg["stops_rev"] >= 1


def test_healthz_does_not_touch_the_database(client) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}
