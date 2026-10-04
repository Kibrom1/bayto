"""M1.10 spike: Python floor-loop driver.

Deliberately NOT importing anything from orchestrator/: M1.10 is architecturally pre-M2,
and orchestrator/ is a heavy FastAPI+SQLAlchemy+Alembic+Postgres service -- the wrong
weight for a spike meant to run as a standalone script against a single sandbox. See
loop.py's module docstring and docs/decisions.md, 2026-10-04.
"""
