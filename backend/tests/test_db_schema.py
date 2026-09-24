"""Schema, migrations, hypertables and constraints, against a real TimescaleDB."""

from __future__ import annotations

import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import t
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.db import migrate
from app.db.models import metadata

pytestmark = pytest.mark.db

BACKEND_DIR = Path(__file__).resolve().parent.parent


def hypertables(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT hypertable_name FROM timescaledb_information.hypertables"))
        return {r[0] for r in rows}


def table_names(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


# ------------------------------------------------------------------ migrations


def test_migrations_create_the_schema_and_are_reversible(fresh_db_url: str) -> None:
    engine = create_engine(fresh_db_url)
    migrate.upgrade(fresh_db_url)
    assert table_names(engine) == {
        "state_events",
        "stops",
        "counter_samples",
        "weld_samples",
        "comm_gaps",
        "collector_state",
    }
    assert hypertables(engine) == {"counter_samples", "weld_samples"}

    migrate.upgrade(fresh_db_url)  # already at head: a no-op, not an error
    migrate.downgrade(fresh_db_url)
    assert table_names(engine) == set()
    migrate.upgrade(fresh_db_url)  # and back up again
    assert "stops" in table_names(engine)
    engine.dispose()


def test_concurrent_migrations_are_serialised(fresh_db_url: str) -> None:
    """The collector and the API both migrate at startup: the advisory lock makes that safe.

    Separate processes, as in production (Alembic's environment is global, not thread-safe).
    """
    code = "import sys; from app.db import migrate; migrate.upgrade(sys.argv[1])"
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", code, fresh_db_url],
            cwd=BACKEND_DIR,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    outcomes = [(p.wait(timeout=120), p.stderr.read() if p.stderr else "") for p in procs]
    assert [rc for rc, _ in outcomes] == [0, 0, 0, 0], outcomes
    engine = create_engine(fresh_db_url)
    assert "stops" in table_names(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0001"
    engine.dispose()


def test_table_definitions_match_the_migrated_database(sync_engine: Engine) -> None:
    """models.py (used to build queries) must not drift from what the migration creates."""
    inspector = inspect(sync_engine)
    for table in metadata.sorted_tables:
        db_columns = {c["name"]: c for c in inspector.get_columns(table.name)}
        assert set(table.c.keys()) == set(db_columns), table.name
        for column in table.c:
            assert db_columns[column.name]["nullable"] == column.nullable or column.primary_key, (
                table.name,
                column.name,
            )


# ------------------------------------------------------------------ hypertables


def test_counter_and_weld_samples_are_partitioned_by_source_time(sync_engine: Engine) -> None:
    with sync_engine.begin() as conn:
        for day in (0, 10, 30):
            conn.execute(
                text("INSERT INTO counter_samples VALUES (:ts, 1, 0, 55, 56)"),
                {"ts": t(6, day=day)},
            )
            conn.execute(
                text("INSERT INTO weld_samples VALUES (:ts, 8.0, 1, true)"), {"ts": t(6, day=day)}
            )
        chunks = conn.execute(
            text(
                "SELECT count(*) FROM timescaledb_information.chunks "
                "WHERE hypertable_name = 'counter_samples'"
            )
        ).scalar_one()
    assert chunks == 3  # 7-day chunks of *simulated* time


def test_replaying_a_sample_is_a_no_op(sync_engine: Engine) -> None:
    insert = text(
        "INSERT INTO counter_samples VALUES (:ts, 10, 1, 55, 56) ON CONFLICT (ts) DO NOTHING"
    )
    with sync_engine.begin() as conn:
        first = conn.execute(insert, {"ts": t(6)}).rowcount
        again = conn.execute(insert, {"ts": t(6)}).rowcount
        total = conn.execute(text("SELECT count(*) FROM counter_samples")).scalar_one()
    assert (first, again, total) == (1, 0, 1)


# ------------------------------------------------------------------ constraints


def insert_stop(conn, start, end=None, state=3, origin="OP20", **extra) -> None:
    duration = (end - start).total_seconds() if end else None
    values = {
        "start_ts": start,
        "end_ts": end,
        "duration_s": duration,
        "state": state,
        "origin_station": origin,
        **extra,
    }
    columns = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    conn.execute(text(f"INSERT INTO stops ({columns}) VALUES ({params})"), values)


def rejected(sync_engine: Engine, statement) -> bool:
    try:
        with sync_engine.begin() as conn:
            statement(conn)
    except IntegrityError:
        return True
    return False


def test_a_stop_is_identified_by_its_start_timestamp(sync_engine: Engine) -> None:
    with sync_engine.begin() as conn:
        insert_stop(conn, t(6), t(6, 10))
    assert rejected(sync_engine, lambda c: insert_stop(c, t(6), t(6, 20)))


def test_at_most_one_stop_and_one_gap_can_be_open(sync_engine: Engine) -> None:
    with sync_engine.begin() as conn:
        insert_stop(conn, t(6))  # open
        insert_stop(conn, t(5), t(5, 30))  # closed ones are fine
    assert rejected(sync_engine, lambda c: insert_stop(c, t(7)))

    with sync_engine.begin() as conn:
        conn.execute(text("INSERT INTO comm_gaps (start_ts) VALUES (:t)"), {"t": t(6)})
    assert rejected(
        sync_engine,
        lambda c: c.execute(text("INSERT INTO comm_gaps (start_ts) VALUES (:t)"), {"t": t(7)}),
    )


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(dict(state=1), id="producing-is-not-a-stop"),
        pytest.param(dict(state=8), id="unknown-state"),
        pytest.param(dict(origin="OP99"), id="unknown-origin"),
        pytest.param(dict(suggested_category="nonsense"), id="unknown-suggestion"),
        pytest.param(dict(qualified_cause="nonsense", qualified_at=t(8)), id="unknown-cause"),
        pytest.param(dict(qualified_cause="other"), id="cause-without-date"),
        pytest.param(dict(qualified_at=t(8)), id="date-without-cause"),
    ],
)
def test_stop_constraints(sync_engine: Engine, bad: dict) -> None:
    assert rejected(sync_engine, lambda c: insert_stop(c, t(6), t(6, 10), **bad))


def test_stop_end_and_duration_must_be_consistent(sync_engine: Engine) -> None:
    assert rejected(sync_engine, lambda c: insert_stop(c, t(6), t(6)))  # empty
    assert rejected(sync_engine, lambda c: insert_stop(c, t(6), t(5)))  # ends before it starts
    assert rejected(
        sync_engine,
        lambda c: c.execute(
            text(
                "INSERT INTO stops (start_ts, end_ts, state, origin_station) VALUES (:a, :b, 3, 'OP20')"
            ),
            {"a": t(6), "b": t(7)},  # closed without a duration
        ),
    )


def test_is_induced_is_generated_from_the_state(sync_engine: Engine) -> None:
    with sync_engine.begin() as conn:
        for i, state in enumerate((3, 4, 5, 6, 7)):
            insert_stop(conn, t(6) + timedelta(hours=i), t(6, 10) + timedelta(hours=i), state=state)
        rows = conn.execute(text("SELECT state, is_induced FROM stops ORDER BY state")).all()
    assert [(s, ind) for s, ind in rows] == [
        (3, False),
        (4, False),
        (5, True),
        (6, True),
        (7, False),
    ]
    with pytest.raises(ProgrammingError), sync_engine.begin() as conn:  # not writable
        conn.execute(text("UPDATE stops SET is_induced = true WHERE state = 3"))


def test_qualification_is_stored_with_its_time(sync_engine: Engine) -> None:
    with sync_engine.begin() as conn:
        insert_stop(conn, t(6), t(6, 10), suggested_category="mechanical_breakdown")
        conn.execute(
            text("UPDATE stops SET qualified_cause = 'electrical_breakdown', qualified_at = :t"),
            {"t": t(7)},
        )
        cause = conn.execute(text("SELECT qualified_cause FROM stops")).scalar_one()
    assert cause == "electrical_breakdown"


def test_state_event_constraints(sync_engine: Engine) -> None:
    def event(conn, scope, ts, state=3, origin=None) -> None:
        conn.execute(
            text(
                "INSERT INTO state_events (scope, ts, state, stop_origin) VALUES (:s, :t, :st, :o)"
            ),
            {"s": scope, "t": ts, "st": state, "o": origin},
        )

    with sync_engine.begin() as conn:
        event(conn, "CELL", t(6), origin=20)
        event(conn, "OP20", t(6))
    assert rejected(sync_engine, lambda c: event(c, "CELL", t(6), origin=20))  # same (scope, ts)
    assert rejected(sync_engine, lambda c: event(c, "CELL", t(7), origin=None))  # CELL needs origin
    assert rejected(sync_engine, lambda c: event(c, "OP10", t(7), origin=10))  # stations have none
    assert rejected(sync_engine, lambda c: event(c, "CELL", t(8), origin=42))
    assert rejected(sync_engine, lambda c: event(c, "LINE", t(8)))
    assert rejected(sync_engine, lambda c: event(c, "OP10", t(8), state=9))


def test_sample_ranges(sync_engine: Engine) -> None:
    def counter(conn, good=1, scrap=0, tct=55.0) -> None:
        conn.execute(
            text("INSERT INTO counter_samples VALUES (:t, :g, :s, :tct, 56)"),
            {"t": t(6), "g": good, "s": scrap, "tct": tct},
        )

    with sync_engine.begin() as conn:
        counter(conn, good=2**32 - 1)  # UInt32 max fits (hence bigint)
    assert rejected(sync_engine, lambda c: counter(c, good=2**32))
    assert rejected(sync_engine, lambda c: counter(c, good=-1))
    assert rejected(sync_engine, lambda c: counter(c, tct=0))


def test_collector_state_is_a_single_row(sync_engine: Engine) -> None:
    with sync_engine.begin() as conn:
        conn.execute(text("INSERT INTO collector_state (last_seen_ts) VALUES (:t)"), {"t": t(6)})
    assert rejected(
        sync_engine,
        lambda c: c.execute(
            text("INSERT INTO collector_state (last_seen_ts) VALUES (:t)"), {"t": t(7)}
        ),
    )
    assert rejected(
        sync_engine,
        lambda c: c.execute(
            text("INSERT INTO collector_state (id, last_seen_ts) VALUES (2, :t)"), {"t": t(7)}
        ),
    )
