"""Real-time pacing: releases the engine's events on the simulated clock."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from .clock import SimClock
from .engine import Simulation
from .model import TagChange
from .persistence import StateStore

log = logging.getLogger(__name__)

Publish = Callable[[list[TagChange]], Awaitable[None]]

# Extra simulated time added to an unclean-save resume point, on top of what can be published
# between two saves (2 x interval x acceleration): scheduling jitter, a slow disk, etc.
_LOOKAHEAD_MARGIN_S = 60.0


def _take_debug_fault(path: Path) -> tuple[int, float]:
    """Consume the trigger file: optional content ``<fault code> <duration s>``."""
    parts = path.read_text().split()
    path.unlink(missing_ok=True)
    code = int(parts[0]) if parts else 202
    duration_s = float(parts[1]) if len(parts) > 1 else 600.0
    return code, duration_s


async def run(
    sim: Simulation,
    clock: SimClock,
    publish: Publish,
    stop: asyncio.Event,
    store: StateStore | None = None,
    save_interval_s: float = 5.0,
    debug_fault_file: Path | None = None,
) -> None:
    """Drive ``sim`` until ``stop`` is set, then write a clean retentive save.

    ``sim.start()`` must already have been called and its initial image published.
    Events that are due are released in batches; no catch-up burst is ever skipped or delayed,
    the simulation simply runs as fast as it can when it lags behind the clock.
    """
    loop = asyncio.get_running_loop()
    next_save = loop.time() + save_interval_s
    lookahead = 2 * save_interval_s * clock.acceleration + _LOOKAHEAD_MARGIN_S

    async def save(*, clean: bool) -> None:
        if store is None:
            return
        snap = sim.snapshot(clean=clean, lookahead_s=lookahead)
        await asyncio.to_thread(store.save, snap)

    try:
        while not stop.is_set():
            batch: list[TagChange] = []
            now = clock.now()
            while sim.next_event_time <= now and len(batch) < 5000:
                batch.extend(sim.step())
            if (
                debug_fault_file is not None
                and debug_fault_file.exists()
                and sim.next_event_time > now
            ):
                code, duration_s = _take_debug_fault(debug_fault_file)
                forced = sim.force_fault(code, duration_s, now)
                log.info("debug fault %s for %.0f s: %s", code, duration_s, bool(forced))
                batch.extend(forced or [])
            if batch:
                await publish(batch)
            if store is not None and loop.time() >= next_save:
                await save(clean=False)
                next_save = loop.time() + save_interval_s
            delay = clock.wall_delay(sim.next_event_time)
            if delay > 0:
                wait_s = min(delay, save_interval_s)
                if debug_fault_file is not None:
                    wait_s = min(wait_s, 0.2)  # poll the trigger file promptly
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=wait_s)
            else:
                await asyncio.sleep(0)  # lagging: yield to the OPC UA server, then catch up
    finally:
        await save(clean=True)
        log.info("stopped at simulated time %s", sim.now)
