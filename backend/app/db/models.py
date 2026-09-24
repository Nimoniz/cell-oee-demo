"""Table definitions (SQLAlchemy Core) used to build queries.

The schema itself is owned by the Alembic migrations, which spell the DDL out in SQL: hypertables,
partial unique indexes and generated columns are clearer there than in metadata. A test compares
these definitions with the migrated database so the two cannot drift apart.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Computed,
    DateTime,
    Float,
    Identity,
    MetaData,
    SmallInteger,
    Table,
    Text,
)

metadata = MetaData()

_TS = DateTime(timezone=True)

state_events = Table(
    "state_events",
    metadata,
    Column("scope", Text, primary_key=True),
    Column("ts", _TS, primary_key=True),
    Column("state", SmallInteger, nullable=False),
    Column("fault_code", SmallInteger, nullable=False, server_default="0"),
    Column("stop_origin", SmallInteger),  # CELL scope only
)

stops = Table(
    "stops",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("start_ts", _TS, nullable=False, unique=True),
    Column("end_ts", _TS),  # NULL while the stop is open
    Column("duration_s", Float),
    Column("state", SmallInteger, nullable=False),
    Column("origin_station", Text, nullable=False),
    Column("fault_code", SmallInteger, nullable=False, server_default="0"),
    Column("suggested_category", Text),
    Column("is_induced", Boolean, Computed("state IN (5, 6)", persisted=True)),
    Column("qualified_cause", Text),
    Column("qualified_at", _TS),
)

counter_samples = Table(
    "counter_samples",
    metadata,
    Column("ts", _TS, primary_key=True),
    Column("good_total", BigInteger, nullable=False),
    Column("scrap_total", BigInteger, nullable=False),
    Column("theoretical_cycle_s", Float, nullable=False),
    Column("last_cycle_s", Float, nullable=False),
)

weld_samples = Table(
    "weld_samples",
    metadata,
    Column("ts", _TS, primary_key=True),
    Column("current_ka", Float, nullable=False),
    Column("points_since_cap_change", BigInteger, nullable=False),
    Column("ok", Boolean, nullable=False),
)

comm_gaps = Table(
    "comm_gaps",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("start_ts", _TS, nullable=False, unique=True),
    Column("end_ts", _TS),  # NULL while the gap is open
)

collector_state = Table(
    "collector_state",
    metadata,
    Column("id", SmallInteger, primary_key=True, server_default="1"),
    Column("last_seen_ts", _TS, nullable=False),
    Column("updated_at", _TS, nullable=False, server_default="now()"),
)
