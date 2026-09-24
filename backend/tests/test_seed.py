"""The history-bootstrap step: fast-forwards the simulator straight into the database."""

from __future__ import annotations

import json

import pytest
from sim_helpers import sim_config
from sqlalchemy import text

from app.collector.seed import already_seeded, seed_history
from app.config import load_settings
from app.db import migrate

pytestmark = pytest.mark.db


def rows(sync_engine, sql: str) -> list[tuple]:
    with sync_engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql)).all()]


async def test_seed_history_migrates_an_unmigrated_database(fresh_db_url: str, tmp_path) -> None:
    """`fresh_db_url` is a database Alembic has never touched — the same state a container's
    first-ever `docker compose up` finds it in. Migrating is `seed_history`'s own job; it must
    not assume something else (the collector, the API) ran `alembic upgrade` first."""
    settings = load_settings().model_copy(update={"database_url": fresh_db_url})
    sim_cfg_path = tmp_path / "sim_config.yaml"
    cfg = sim_config()
    import yaml

    sim_cfg_path.write_text(yaml.safe_dump(cfg.model_dump(mode="json")), encoding="utf-8")

    await seed_history(settings, sim_cfg_path, days=0.02)
    assert await already_seeded(fresh_db_url)


async def test_seed_history_populates_the_database(fresh_db_url: str, tmp_path) -> None:
    migrate.upgrade(fresh_db_url)
    settings = load_settings().model_copy(update={"database_url": fresh_db_url})
    sim_cfg_path = tmp_path / "sim_config.yaml"
    state_path = tmp_path / "state" / "plc_state.json"
    cfg = sim_config(persistence={"enabled": True, "path": str(state_path)})
    import yaml

    sim_cfg_path.write_text(yaml.safe_dump(cfg.model_dump(mode="json")), encoding="utf-8")

    assert not await already_seeded(fresh_db_url)
    await seed_history(settings, sim_cfg_path, days=0.05)  # ~72 simulated minutes
    assert await already_seeded(fresh_db_url)

    from sqlalchemy import create_engine

    sync_engine = create_engine(fresh_db_url)
    events = rows(sync_engine, "SELECT count(*) FROM state_events")
    counters = rows(sync_engine, "SELECT count(*) FROM counter_samples")
    last_seen = rows(sync_engine, "SELECT last_seen_ts FROM collector_state")
    assert events[0][0] > 0
    assert counters[0][0] > 0
    assert last_seen[0][0] is not None

    # The simulator's own retentive state was written: a live simulator would resume right here.
    assert state_path.exists()
    saved = json.loads(state_path.read_text())
    assert saved["clean_shutdown"] is True
    assert saved["sim_time"] >= cfg.clock.start_ts + 0.05 * 86_400 - 60
    sync_engine.dispose()


async def test_seeding_twice_is_a_no_op_unless_forced(fresh_db_url: str, tmp_path) -> None:
    migrate.upgrade(fresh_db_url)
    settings = load_settings().model_copy(update={"database_url": fresh_db_url})
    sim_cfg_path = tmp_path / "sim_config.yaml"
    state_path = tmp_path / "state" / "plc_state.json"
    cfg = sim_config(persistence={"enabled": True, "path": str(state_path)})
    import yaml

    sim_cfg_path.write_text(yaml.safe_dump(cfg.model_dump(mode="json")), encoding="utf-8")

    await seed_history(settings, sim_cfg_path, days=0.02)
    from sqlalchemy import create_engine

    sync_engine = create_engine(fresh_db_url)
    first_count = rows(sync_engine, "SELECT count(*) FROM state_events")[0][0]

    await seed_history(settings, sim_cfg_path, days=0.02)  # already seeded: skipped
    assert rows(sync_engine, "SELECT count(*) FROM state_events")[0][0] == first_count
    sync_engine.dispose()
