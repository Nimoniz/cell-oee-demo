"""Alembic environment.

The database URL comes from ``sqlalchemy.url`` (set by ``app.db.migrate``) or ``DATABASE_URL``.
Migrations run under a session-level advisory lock, so the collector and the API can both call
``upgrade head`` at startup without racing.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool, text

config = context.config

_LOCK_KEY = 727_274_001  # arbitrary, shared by every process that migrates this database


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("no database URL: set sqlalchemy.url or DATABASE_URL")
    return url


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _LOCK_KEY})
        connection.commit()
        try:
            context.configure(connection=connection, target_metadata=None)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _LOCK_KEY})
            connection.commit()


if context.is_offline_mode():
    raise RuntimeError("offline migrations are not supported (TimescaleDB DDL needs a server)")
run_migrations_online()
