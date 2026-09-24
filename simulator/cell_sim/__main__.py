"""Entry point: ``python -m cell_sim [--config config.yaml] [--acceleration N]``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from pathlib import Path

from .clock import SimClock
from .config import SimConfig, load_config
from .engine import Simulation
from .opcua_server import OpcUaPlc
from .pacer import run
from .persistence import StateStore

log = logging.getLogger("cell_sim")


def _install_stop_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows event loop
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))


def _warn_if_freeze_undetectable(cfg: SimConfig) -> None:
    """The collector's watchdog waits max(3 s wall, 5 s simulated), i.e. 3 s x acceleration."""
    freeze = cfg.chaos.heartbeat_freeze
    if cfg.chaos.enabled and freeze is not None:
        detectable_after_s = max(5.0, 3.0 * cfg.clock.acceleration)
        if freeze.duration_s <= detectable_after_s:
            log.warning(
                "chaos heartbeat_freeze of %.0f s will not be detected at x%s "
                "(the watchdog needs more than %.0f simulated seconds)",
                freeze.duration_s,
                cfg.clock.acceleration,
                detectable_after_s,
            )


async def serve(cfg: SimConfig, stop: asyncio.Event | None = None) -> None:
    stop = stop or asyncio.Event()
    store = StateStore(cfg.persistence.path) if cfg.persistence.enabled else None
    restored = store.load() if store else None
    if restored:
        log.info("retentive data found: resuming at simulated %s", restored.resume_ts)

    _warn_if_freeze_undetectable(cfg)
    sim = Simulation(cfg, restored)
    clock = SimClock(sim.start_ts, cfg.clock.acceleration)
    async with OpcUaPlc(cfg.opcua) as plc:
        await plc.publish(sim.start())  # initial image, before any client can connect
        await plc.start()
        log.info(
            "OPC UA server up on %s (namespace %s), x%s",
            cfg.opcua.endpoint,
            cfg.opcua.namespace_uri,
            cfg.clock.acceleration,
        )
        debug_file = os.environ.get("CELL_SIM_DEBUG_FAULT_FILE")
        await run(
            sim,
            clock,
            plc.publish,
            stop,
            store,
            cfg.persistence.save_interval_s,
            Path(debug_file) if debug_file else None,
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="cell_sim", description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path(os.environ.get("CELL_SIM_CONFIG", "config.yaml"))
    )
    parser.add_argument(
        "--acceleration",
        type=float,
        default=float(os.environ["ACCELERATION"]) if os.environ.get("ACCELERATION") else None,
        help="override clock.acceleration (1..60); defaults to $ACCELERATION when set",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("asyncua").setLevel(logging.WARNING)

    cfg = load_config(args.config)
    if args.acceleration is not None:
        if not 1 <= args.acceleration <= 60:
            parser.error("--acceleration must be between 1 and 60")
        cfg = cfg.model_copy(
            update={"clock": cfg.clock.model_copy(update={"acceleration": args.acceleration})}
        )

    async def amain() -> None:
        stop = asyncio.Event()
        _install_stop_handlers(stop)
        await serve(cfg, stop)

    asyncio.run(amain())


if __name__ == "__main__":
    main()
