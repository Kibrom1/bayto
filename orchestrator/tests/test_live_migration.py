"""Runs the M2.2 migration against a real Postgres pointed to by DATABASE_URL, then downgrades
back to base to leave it clean. Skips (does not fail) when no live Postgres is reachable --
this repo's dev-team sandbox has none by default; see orchestrator/README.md for how this was
verified manually against a throwaway Docker container.
"""
import os

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

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
def live_db():
    if not _live_postgres_reachable():
        pytest.skip(f"no live Postgres reachable at DATABASE_URL ({database_url()!r}); "
                    "set DATABASE_URL to test against one")
    cfg = Config(ALEMBIC_INI)
    yield cfg
    command.downgrade(cfg, "base")


def test_upgrade_head_creates_every_table(live_db):
    command.upgrade(live_db, "head")

    engine = create_engine(database_url())
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
    engine.dispose()

    assert {"task", "agent", "mode", "session", "session_agent", "sandbox", "turn", "artifact",
            "message", "report"} <= tables
