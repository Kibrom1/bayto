"""Runs the M2.2 migration against a real Postgres pointed to by DATABASE_URL. See
conftest.py's `live_schema` fixture for the skip-cleanly-when-unreachable / upgrade-then-
downgrade lifecycle -- this file only asserts on the result.
"""
from sqlalchemy import create_engine, inspect

from orchestrator.db import database_url


def test_upgrade_head_creates_every_table(live_schema):
    engine = create_engine(database_url())
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
    engine.dispose()

    assert {"task", "agent", "mode", "session", "session_agent", "sandbox", "turn", "artifact",
            "message", "report"} <= tables
