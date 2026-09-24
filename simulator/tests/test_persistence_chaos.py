from __future__ import annotations

from pathlib import Path

import pytest
from conftest import HOUR, make_config, replay

from cell_sim.engine import Simulation
from cell_sim.model import TagChange
from cell_sim.persistence import PersistedState, StateStore

START = make_config().clock.start_ts


def run(cfg, until: float, restored: PersistedState | None = None):
    sim = Simulation(cfg, restored)
    changes = sim.start() + sim.run_until(until)
    return sim, changes


def last(changes: list[TagChange], path: str) -> int:
    return int(next(c.value for c in reversed(changes) if c.path == path))


# ------------------------------------------------------------------ retentive data


def test_clean_restart_resumes_time_counters_and_wear(tmp_path: Path) -> None:
    cfg = make_config()
    sim, first = run(cfg, START + 8 * HOUR)
    store = StateStore(tmp_path / "state" / "plc.json")
    store.save(sim.snapshot(clean=True))

    restored = store.load()
    assert restored is not None
    sim2 = Simulation(cfg, restored)
    second = sim2.start() + sim2.run_until(sim.now + 8 * HOUR)

    # Simulated time resumes exactly where it stopped, never before.
    assert sim2.start_ts == sim.now
    assert min(c.ts for c in second) >= max(c.ts for c in first)
    # Counters and the wear state carry over (the first image already holds the restored values).
    init = {c.path: c.value for c in second if c.ts == sim.now}
    assert init["Cell/GoodCount"] == last(first, "Cell/GoodCount")
    assert init["Cell/ScrapCount"] == last(first, "Cell/ScrapCount")
    assert init["OP20/Weld/PointsSinceCapChange"] == last(first, "OP20/Weld/PointsSinceCapChange")
    assert restored.wear.parts_since_cap_change > 0
    # ...and keep growing: no counter ever goes backwards across the restart.
    for path in ("Cell/GoodCount", "Cell/ScrapCount", "Cell/Heartbeat"):
        values = [int(c.value) for c in first + second if c.path == path]
        assert values == sorted(values), path


def test_restart_ignores_configured_start_date(tmp_path: Path) -> None:
    cfg = make_config()
    sim, _ = run(cfg, START + HOUR)
    later = make_config(clock={"start": "2020-01-01T00:00:00"})  # earlier than the saved time
    sim2 = Simulation(later, sim.snapshot(clean=True))
    assert sim2.start_ts == sim.now


def test_crash_never_makes_time_go_backwards() -> None:
    cfg = make_config()
    sim, _ = run(cfg, START + 2 * HOUR)
    saved = sim.snapshot(clean=False, lookahead_s=1800)  # periodic save, then a crash...
    published = sim.run_until(sim.now + 600)  # ...10 sim-minutes after the save
    assert saved.resume_ts >= max(c.ts for c in published)

    sim2 = Simulation(cfg, saved)
    changes = sim2.start() + sim2.run_until(saved.resume_ts + HOUR)
    assert min(c.ts for c in changes) >= max(c.ts for c in published)


def test_snapshot_never_older_than_published_data() -> None:
    sim, changes = run(make_config(), START + HOUR)
    snap = sim.snapshot(clean=True, now=sim.now - 500)  # a lagging pacer clock
    assert snap.sim_time >= max(c.ts for c in changes)


def test_state_store_roundtrip_and_bad_files(tmp_path: Path) -> None:
    sim, _ = run(make_config(), START + HOUR)
    path = tmp_path / "plc.json"
    store = StateStore(path)
    assert store.load() is None  # first boot
    snap = sim.snapshot(clean=True)
    store.save(snap)
    assert store.load() == snap
    assert not list(tmp_path.glob("*.tmp"))  # atomic write leaves no temp file
    path.write_text("{not json", encoding="utf-8")
    assert store.load() is None  # corrupted: start fresh rather than crash


# ------------------------------------------------------------------ chaos


def test_chaos_is_off_by_default_even_if_configured() -> None:
    cfg = make_config(chaos={"counters_at_start": {"good": 5, "scrap": 6}})
    _, changes = run(cfg, START + 10)
    assert last(changes, "Cell/GoodCount") == 0


def test_chaos_counter_reset_overrides_retained_counters() -> None:
    cfg = make_config()
    sim, _ = run(cfg, START + 4 * HOUR)
    snap = sim.snapshot(clean=True)
    assert snap.good_count > 100

    chaotic = make_config(chaos={"enabled": True, "counters_at_start": {"good": 0, "scrap": 0}})
    sim2 = Simulation(chaotic, snap)
    init = {c.path: c.value for c in sim2.start()}
    assert init["Cell/GoodCount"] == 0
    assert init["Cell/ScrapCount"] == 0
    assert sim2.cell.heartbeat == snap.heartbeat  # only the counters are reset


def test_chaos_counter_rollover() -> None:
    cfg = make_config(
        chaos={
            "enabled": True,
            "counters_at_start": {"good": 2**32 - 3, "scrap": 2**32 - 1},
        },
        cell={"base_scrap_rate": 0.5},
    )
    _, changes = run(cfg, START + HOUR)
    good = [int(c.value) for c in changes if c.path == "Cell/GoodCount"]
    scrap = [int(c.value) for c in changes if c.path == "Cell/ScrapCount"]
    assert good[0] == 2**32 - 3 and scrap[0] == 2**32 - 1
    assert any(b < a for a, b in zip(good, good[1:], strict=False))  # wrapped to a small value
    assert any(b < a for a, b in zip(scrap, scrap[1:], strict=False))
    assert all(0 <= v <= 2**32 - 1 for v in good + scrap)


def test_chaos_periodic_counter_reset_and_heartbeat_freeze() -> None:
    cfg = make_config(
        chaos={
            "enabled": True,
            "counter_reset_every_h": 2,
            "heartbeat_freeze": {"every_h": 3, "duration_s": 20},
        }
    )
    _, changes = run(cfg, START + 7 * HOUR)  # START is 05:00: resets at 06:00, 08:00, 10:00, 12:00
    reset_ts = [
        c.ts for c in changes if c.path == "Cell/GoodCount" and c.value == 0 and c.ts > START
    ]
    assert reset_ts[:3] == [START + HOUR, START + 3 * HOUR, START + 5 * HOUR]

    # Heartbeat stalls for 20 s at 06:00 (multiples of 3 h: 06:00, 09:00, 12:00).
    hb = [
        c.ts
        for c in changes
        if c.path == "Cell/Heartbeat" and START + HOUR - 5 <= c.ts <= START + HOUR + 40
    ]
    assert not any(START + HOUR < t < START + HOUR + 20 for t in hb)
    assert any(t >= START + HOUR + 20 for t in hb)
    assert list(replay(changes))  # image stays consistent


@pytest.mark.parametrize(
    "bad", [{"counter_reset_every_h": 0}, {"heartbeat_freeze": {"every_h": 1}}]
)
def test_chaos_config_validation(bad: dict) -> None:
    with pytest.raises(ValueError):
        make_config(chaos=bad)
