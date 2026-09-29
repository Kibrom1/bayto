"""Async SQLAlchemy engine setup (M2.1 scaffold). Schema/models land in M2.2.

No live Postgres is required to import this module or create the engine: SQLAlchemy
engines connect lazily on first use, so `get_engine()` is safe to call without a
reachable database.
"""
from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

DEFAULT_DATABASE_URL = "postgresql+psycopg://bayto:bayto@localhost:5432/bayto"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(database_url(), pool_pre_ping=True)


def get_sessionmaker() -> async_sessionmaker:
    return async_sessionmaker(get_engine(), expire_on_commit=False)
