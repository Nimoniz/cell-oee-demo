"""FastAPI dependencies: the database connection and the shared settings/hub."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import Settings

from .live_hub import LiveHub


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_hub(request: Request) -> LiveHub:
    return request.app.state.hub


async def get_conn(request: Request) -> AsyncIterator[AsyncConnection]:
    """Read-only: never committed, rolled back (harmlessly) when the connection is released."""
    async with request.app.state.engine.connect() as conn:
        yield conn


async def get_write_conn(request: Request) -> AsyncIterator[AsyncConnection]:
    """Committed on a normal return, rolled back if the handler raises."""
    async with request.app.state.engine.begin() as conn:
        yield conn
