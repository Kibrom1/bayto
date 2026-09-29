# orchestrator

Python (FastAPI) service for M2: sessions, the acp message mirror into Postgres, and SSE streaming.
M2.1 scaffold only — see `docs/work-plan.md` for M2.2+ (schema, mirror, SandboxProvider, API, moderator).

## Layout

- `pyproject.toml` — uv-managed project (`bayto-orchestrator`); depends on `acp` from `../agent-comms` as an
  editable path dependency (`[tool.uv.sources]`), so it's a real installed package, not copy-pasted code.
- `src/orchestrator/app.py` — FastAPI app with a `/healthz` endpoint.
- `src/orchestrator/db.py` — async SQLAlchemy engine (`postgresql+psycopg`), reads `DATABASE_URL` (defaults to
  `postgresql+psycopg://bayto:bayto@localhost:5432/bayto`). No schema/models yet (M2.2).
- `alembic.ini` / `alembic/env.py` — async Alembic setup wired to `orchestrator.db.database_url()`. No revisions
  exist yet (`alembic/versions/` is empty); `target_metadata` is `None` until M2.2 defines the schema.
- `tests/` — `test_health.py` (the one required passing test), plus `test_acp_dependency.py` and `test_db.py`.

## Run tests

    cd orchestrator
    uv venv --python 3.12
    uv sync --extra dev
    .venv/bin/pytest -q

None of the tests require a reachable Postgres.

## Database / Alembic caveat

No live Postgres is available in this dev-team sandbox. `alembic heads`/`alembic history` (file-only, no DB
connection) work and confirm the config loads. `alembic current`/`upgrade` correctly load `env.py` and build the
async engine from `DATABASE_URL`, then fail only at the actual TCP connection (`Connection refused` to
`localhost:5432`) — i.e. the wiring is real, but running migrations against a live database has not been
verified end to end here. Point `DATABASE_URL` at a reachable Postgres to do that.
