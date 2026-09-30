"""Async Alembic environment, wired to orchestrator.db (DATABASE_URL) and orchestrator.models
(M2.2 defines the schema; target_metadata now points at it, so `--autogenerate` has something
to diff against).
"""
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from orchestrator.db import database_url
from orchestrator.models import Base

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers=False: fileConfig's default (True) silently disables every
    # logging.getLogger(...) already created at import time -- e.g. orchestrator.moderator's
    # module-level loggers, imported before tests/conftest.py's live_schema fixture runs this
    # migration -- which then makes log.warning(...) calls silently no-op for the rest of the
    # process (Logger.disabled short-circuits before a record is even created). M2.7 found
    # this the hard way (see docs/decisions.md, 2026-09-30).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = database_url()
    connectable = async_engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
