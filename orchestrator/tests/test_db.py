"""Engine construction must not require a reachable Postgres (SQLAlchemy connects lazily)."""
from orchestrator.db import database_url, get_engine


def test_database_url_defaults_without_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert database_url().startswith("postgresql+psycopg://")


def test_database_url_reads_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:y@example.invalid:5432/db")
    assert database_url() == "postgresql+psycopg://x:y@example.invalid:5432/db"


def test_get_engine_does_not_connect():
    engine = get_engine()
    assert str(engine.url).startswith("postgresql+psycopg://")
