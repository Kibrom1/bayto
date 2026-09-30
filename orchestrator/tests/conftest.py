"""Shared live-Postgres fixtures. Every test that needs a real database skips cleanly
(pytest.skip, never fails or hangs) when none is reachable at DATABASE_URL -- see
orchestrator/README.md for how this was verified against a throwaway Docker container.

Also installs the M2.10 test-wide OpenTelemetry TracerProvider (session-scoped, autouse):
`opentelemetry.trace.set_tracer_provider()` only ever succeeds once per process, so this
MUST claim it before `app.py`'s `lifespan` (which calls `telemetry.configure_tracing()`)
gets a chance to -- whichever caller gets there first wins, and this fixture is
session-scoped + autouse specifically so it always runs before the first test, regardless
of which test file pytest happens to collect/run first. `test_health.py`'s `TestClient(app)`
DOES trigger `lifespan` (unlike `test_api_sessions.py`'s bare `ASGITransport`), so without
this ordering guarantee a real Console/OTLP provider could win the race instead and every
span-inspecting test would silently record into the wrong (or no) exporter.
"""
import os

import psycopg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from orchestrator.db import database_url

ALEMBIC_INI = os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini")

_SPAN_EXPORTER = InMemorySpanExporter()


@pytest.fixture(scope="session", autouse=True)
def _tracer_provider():
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_SPAN_EXPORTER))  # synchronous: spans
    trace.set_tracer_provider(provider)                                # land immediately


@pytest.fixture
def span_exporter(_tracer_provider):
    """Yields the shared InMemorySpanExporter, cleared before the test so each test only
    sees its own spans."""
    _SPAN_EXPORTER.clear()
    yield _SPAN_EXPORTER
    _SPAN_EXPORTER.clear()


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
