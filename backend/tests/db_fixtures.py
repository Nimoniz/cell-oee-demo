"""Database fixtures: a TimescaleDB container (or ``TEST_DATABASE_URL``) and migrated databases."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine, make_url

from app.db import migrate

IMAGE = "timescale/timescaledb:latest-pg16"


def _with_database(url: str, database: str) -> str:
    parsed: URL = make_url(url).set(database=database)
    return parsed.render_as_string(hide_password=False)


def create_database(admin_url: str) -> str:
    """A new empty database on the test server; returns its URL."""
    name = f"t_{uuid.uuid4().hex[:12]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    return _with_database(admin_url, name)


@pytest.fixture(scope="session")
def admin_url() -> Iterator[str]:
    """Server-level URL: an existing server via TEST_DATABASE_URL, else a throwaway container."""
    existing = os.environ.get("TEST_DATABASE_URL")
    if existing:
        yield existing
        return
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    try:
        from testcontainers.community.postgres import PostgresContainer
    except ImportError:  # older testcontainers
        from testcontainers.postgres import PostgresContainer

    with PostgresContainer(IMAGE, username="oee", password="oee", dbname="postgres") as pg:
        host = pg.get_container_host_ip()
        port = pg.get_exposed_port(5432)
        yield f"postgresql+psycopg://oee:oee@{host}:{port}/postgres"


@pytest.fixture
def fresh_db_url(admin_url: str) -> str:
    """A new, empty (not migrated) database."""
    return create_database(admin_url)


@pytest.fixture(scope="module")
def migrated_db_url(admin_url: str) -> str:
    """A database migrated to head, shared by the tests of a module (they truncate it)."""
    url = create_database(admin_url)
    migrate.upgrade(url)
    return url


TABLES = (
    "state_events",
    "stops",
    "counter_samples",
    "weld_samples",
    "comm_gaps",
    "collector_state",
)


@pytest.fixture
def sync_engine(migrated_db_url: str) -> Iterator[Engine]:
    engine = create_engine(migrated_db_url)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)}"))
    yield engine
    engine.dispose()
