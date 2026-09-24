"""Entry point: ``python -m app.collector``."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from sqlalchemy.ext.asyncio import create_async_engine

from app.config import load_settings
from app.db import migrate

from .pipeline import Collector

log = logging.getLogger("collector")


def _install_stop_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows event loop
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))


async def amain() -> None:
    settings = load_settings()
    log.info("migrating database")
    await asyncio.to_thread(migrate.upgrade, settings.database_url)
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    stop = asyncio.Event()
    _install_stop_handlers(stop)
    try:
        await Collector(settings, engine).run(stop)
    finally:
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("asyncua").setLevel(logging.WARNING)
    if sys.platform == "win32":  # psycopg's async mode cannot use the default Proactor loop
        asyncio.run(amain(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(amain())


if __name__ == "__main__":
    main()
