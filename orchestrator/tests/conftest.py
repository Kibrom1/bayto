"""Shared live-Postgres fixtures. Every test that needs a real database skips cleanly
(pytest.skip, never fails or hangs) when none is reachable at DATABASE_URL -- see
orchestrator/README.md for how this was verified against a throwaway Docker container.
"""
import os

import psycopg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from orchestrator.db import database_url

ALEMBIC_INI = os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini")


def _live_postgres_reachable() -> bool:
    conninfo = database_url().replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        with psycopg.connect(conninfo, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


@pytest.fixture
def live_schema():
    """Skips if unreachable; otherwise yields an Alembic Config already migrated to head,
    and downgrades back to base on teardown so the database is left clean."""
    if not _live_postgres_reachable():
        pytest.skip(f"no live Postgres reachable at DATABASE_URL ({database_url()!r}); "
                    "set DATABASE_URL to test against one")
    cfg = Config(ALEMBIC_INI)
    command.upgrade(cfg, "head")
    yield cfg
    command.downgrade(cfg, "base")


@pytest_asyncio.fixture
async def live_sessionmaker(live_schema):
    """A fresh async_sessionmaker bound to the live, migrated database."""
    engine = create_async_engine(database_url())
    sm = async_sessionmaker(engine, expire_on_commit=False)
    yield sm
    await engine.dispose()
