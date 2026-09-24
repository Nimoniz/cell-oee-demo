"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings, load_settings
from app.db import migrate

from .live_hub import LiveHub
from .routes import cell, oee, stops, weld

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        log.info("migrating database")
        await asyncio.to_thread(migrate.upgrade, settings.database_url)
        app.state.engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        app.state.hub = LiveHub(app.state.engine, settings)
        stop = asyncio.Event()
        app.state._hub_stop = stop
        hub_task = asyncio.create_task(app.state.hub.run(stop), name="live-hub")
        try:
            yield
        finally:
            stop.set()
            hub_task.cancel()
            await asyncio.gather(hub_task, return_exceptions=True)
            await app.state.engine.dispose()

    app = FastAPI(title="Cell OEE Demo API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.api.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for router in (cell.router, oee.router, stops.router, weld.router):
        app.include_router(router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, bool]:
        """Liveness only (the process is up and serving) — the Docker healthcheck target.
        Deliberately does not touch the database: a slow DB should not flip the container
        unhealthy and get it killed mid-migration or mid-query."""
        return {"ok": True}

    return app
