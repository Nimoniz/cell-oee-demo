"""One-shot history bootstrap, reused by day 8's ``docker compose up``.

Runs the simulator's deterministic engine flat out (no OPC UA, no real-time pacing) for
``days`` of simulated history and feeds the resulting tag changes straight through the
collector's own tracker into the database — the exact same code path a live collector uses, so
the seeded history is indistinguishable from one collected live.

It then writes the simulator's own retentive state file (the one ``simulator/config.yaml``
points ``persistence.path`` at). When the real ``simulator`` container starts afterwards, it
resumes from exactly that point — the live stream continues seamlessly where the seed left off.

Idempotent: if the database already has a ``CELL`` state event, seeding is skipped. This makes it
safe to run as a docker-compose step on every ``up``, not just the first one.

This module imports ``cell_sim`` (the simulator package). The image that runs it must have that
package installed alongside the backend — see ``simulator/pyproject.toml``; wired into
docker-compose at day 8.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings, load_settings
from app.db import migrate

from .store import CollectorStore
from .tracker import StopTracker
from .types import HEARTBEAT, Group

log = logging.getLogger("seed")

_GROUP_BATCH = 500  # groups per transaction: enough to be fast, small enough to stay memory-light


def _as_groups(changes: list, to_datetime) -> list[Group]:
    by_ts: dict = {}
    for c in changes:
        if c.path == HEARTBEAT:
            continue
        ts = to_datetime(c.ts)  # the simulator's raw ts is POSIX seconds, not a datetime
        by_ts.setdefault(ts, Group(ts)).values[c.path] = c.value
    return [by_ts[ts] for ts in sorted(by_ts)]


async def already_seeded(database_url: str) -> bool:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(text("SELECT 1 FROM state_events WHERE scope = 'CELL' LIMIT 1"))
            ).first()
            return row is not None
    finally:
        await engine.dispose()


async def seed_history(
    settings: Settings, sim_config_path: Path, days: float, force: bool = False
) -> None:
    from cell_sim.config import load_config
    from cell_sim.engine import Simulation
    from cell_sim.model import to_datetime
    from cell_sim.persistence import StateStore

    log.info("migrating database")
    await asyncio.to_thread(migrate.upgrade, settings.database_url)

    if not force and await already_seeded(settings.database_url):
        log.info("database already has history: skipping seed")
        return

    sim_cfg = load_config(sim_config_path)
    sim = Simulation(sim_cfg)
    end_ts = sim_cfg.clock.start_ts + days * 86_400

    engine = create_async_engine(settings.database_url)
    try:
        store = CollectorStore(engine)  # no NOTIFY: nobody is listening during the seed
        tracker = StopTracker()
        changes = sim.start()
        groups = _as_groups(changes, to_datetime)
        n_groups = 0
        n_ops = 0
        while sim.next_event_time <= end_ts:
            groups += _as_groups(sim.step(), to_datetime)
            if len(groups) >= _GROUP_BATCH:
                n_ops += await _flush(store, tracker, groups)
                n_groups += len(groups)
                groups = []
        n_ops += await _flush(store, tracker, groups)
        n_groups += len(groups)
        log.info("seeded %.2f days: %d groups, %d database operations", days, n_groups, n_ops)

        # Hand off to the live simulator: it resumes its own clock, counters and electrode wear
        # from exactly here (see simulator persistence.py); the collector resumes from the
        # collector_state row that the last _flush wrote.
        snap = sim.snapshot(clean=True)
        StateStore(Path(sim_cfg.persistence.path)).save(snap)
        log.info("wrote simulator retentive state at simulated %s", to_datetime(sim.now))
    finally:
        await engine.dispose()


async def _flush(store: CollectorStore, tracker: StopTracker, groups: list[Group]) -> int:
    if not groups:
        return 0
    ops = []
    for group in groups:
        ops += tracker.apply(group)
    await store.apply(ops, last_seen=groups[-1].ts)
    return len(ops)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sim-config", type=Path, required=True)
    parser.add_argument("--days", type=float, default=3.0)
    parser.add_argument("--force", action="store_true", help="seed even if history already exists")
    args = parser.parse_args()
    coro = seed_history(load_settings(), args.sim_config, args.days, args.force)
    if sys.platform == "win32":  # psycopg's async mode cannot use the default Proactor loop
        asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(coro)


if __name__ == "__main__":
    main()
