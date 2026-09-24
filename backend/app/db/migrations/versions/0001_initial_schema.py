"""Initial schema: events, stops, hypertables for samples, gaps, collector state.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Frozen on purpose: a migration must not change when app.domain changes.
_CAUSES = (
    "mechanical_breakdown",
    "electrical_breakdown",
    "setup_adjustment",
    "tool_change",
    "missing_parts",
    "downstream_saturation",
    "quality_issue",
    "other",
)
_CAUSE_LIST = ", ".join(f"'{c}'" for c in _CAUSES)

_UPGRADE = f"""
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- One row per change of Cell/State, OPn/State. The natural key is the source timestamp, so
-- replaying the same notification (collector restart, subscription re-created) is a no-op.
CREATE TABLE state_events (
    scope        text        NOT NULL,
    ts           timestamptz NOT NULL,
    state        smallint    NOT NULL,
    fault_code   smallint    NOT NULL DEFAULT 0,
    stop_origin  smallint,
    PRIMARY KEY (scope, ts),
    CONSTRAINT ck_state_events_scope  CHECK (scope IN ('CELL', 'OP10', 'OP20', 'OP30')),
    CONSTRAINT ck_state_events_state  CHECK (state BETWEEN 1 AND 7),
    CONSTRAINT ck_state_events_origin CHECK (stop_origin IS NULL OR stop_origin IN (0, 10, 20, 30, 99)),
    CONSTRAINT ck_state_events_origin_scope CHECK ((scope = 'CELL') = (stop_origin IS NOT NULL))
);

-- A stop is a non-producing period of the cell. end_ts / duration_s are NULL while it is open.
-- There is no is_micro: the threshold is configurable, so it is applied when reading.
CREATE TABLE stops (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    start_ts            timestamptz      NOT NULL,
    end_ts              timestamptz,
    duration_s          double precision,
    state               smallint         NOT NULL,
    origin_station      text             NOT NULL,
    fault_code          smallint         NOT NULL DEFAULT 0,
    suggested_category  text,
    is_induced          boolean GENERATED ALWAYS AS (state IN (5, 6)) STORED,
    qualified_cause     text,
    qualified_at        timestamptz,
    CONSTRAINT uq_stops_start_ts   UNIQUE (start_ts),
    CONSTRAINT ck_stops_state      CHECK (state BETWEEN 2 AND 7),
    CONSTRAINT ck_stops_origin     CHECK (origin_station IN ('OP10', 'OP20', 'OP30', 'CELL')),
    CONSTRAINT ck_stops_end        CHECK (end_ts IS NULL OR end_ts > start_ts),
    CONSTRAINT ck_stops_duration   CHECK ((end_ts IS NULL) = (duration_s IS NULL)),
    CONSTRAINT ck_stops_duration_positive CHECK (duration_s IS NULL OR duration_s > 0),
    CONSTRAINT ck_stops_suggested  CHECK (suggested_category IS NULL OR suggested_category IN ({_CAUSE_LIST})),
    CONSTRAINT ck_stops_cause      CHECK (qualified_cause IS NULL OR qualified_cause IN ({_CAUSE_LIST})),
    CONSTRAINT ck_stops_qualified  CHECK ((qualified_cause IS NULL) = (qualified_at IS NULL))
);
-- At most one open stop: the cell is in one state at a time.
CREATE UNIQUE INDEX uq_stops_single_open ON stops ((true)) WHERE end_ts IS NULL;
CREATE INDEX ix_stops_end_ts ON stops (end_ts);
CREATE INDEX ix_stops_unqualified ON stops (start_ts)
    WHERE qualified_cause IS NULL AND end_ts IS NOT NULL;

-- Raw cumulative counters (UInt32 on the PLC, hence bigint). Deltas are computed when reading.
CREATE TABLE counter_samples (
    ts                   timestamptz      NOT NULL PRIMARY KEY,
    good_total           bigint           NOT NULL,
    scrap_total          bigint           NOT NULL,
    theoretical_cycle_s  double precision NOT NULL,
    last_cycle_s         double precision NOT NULL,
    CONSTRAINT ck_counter_good  CHECK (good_total  BETWEEN 0 AND 4294967295),
    CONSTRAINT ck_counter_scrap CHECK (scrap_total BETWEEN 0 AND 4294967295),
    CONSTRAINT ck_counter_tct   CHECK (theoretical_cycle_s > 0),
    CONSTRAINT ck_counter_cycle CHECK (last_cycle_s >= 0)
);
SELECT create_hypertable('counter_samples', 'ts', chunk_time_interval => INTERVAL '7 days');

CREATE TABLE weld_samples (
    ts                       timestamptz      NOT NULL PRIMARY KEY,
    current_ka               double precision NOT NULL,
    points_since_cap_change  bigint           NOT NULL,
    ok                       boolean          NOT NULL,
    CONSTRAINT ck_weld_points CHECK (points_since_cap_change BETWEEN 0 AND 4294967295)
);
SELECT create_hypertable('weld_samples', 'ts', chunk_time_interval => INTERVAL '7 days');

-- Communication gaps: nothing observed between start_ts and end_ts (NULL while open).
CREATE TABLE comm_gaps (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    start_ts  timestamptz NOT NULL,
    end_ts    timestamptz,
    CONSTRAINT uq_comm_gaps_start_ts UNIQUE (start_ts),
    CONSTRAINT ck_comm_gaps_end      CHECK (end_ts IS NULL OR end_ts > start_ts)
);
CREATE UNIQUE INDEX uq_comm_gaps_single_open ON comm_gaps ((true)) WHERE end_ts IS NULL;
CREATE INDEX ix_comm_gaps_end_ts ON comm_gaps (end_ts);

-- Single row: latest simulated time received (the UI's "now").
CREATE TABLE collector_state (
    id            smallint    PRIMARY KEY DEFAULT 1,
    last_seen_ts  timestamptz NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_collector_state_single CHECK (id = 1)
);
"""

_DOWNGRADE = """
DROP TABLE collector_state;
DROP TABLE comm_gaps;
DROP TABLE weld_samples;
DROP TABLE counter_samples;
DROP TABLE stops;
DROP TABLE state_events;
"""


def upgrade() -> None:
    for statement in _split(_UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    for statement in _split(_DOWNGRADE):
        op.execute(statement)


def _split(script: str) -> list[str]:
    """One statement per execute: psycopg does not mix DDL and SELECT in a multi-statement string."""
    lines = [ln for ln in script.splitlines() if not ln.strip().startswith("--")]
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]
