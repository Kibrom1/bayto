"""Bayto Orchestrator FastAPI app.

M2.8 mounts the task/session REST API on top of the M2.1-M2.7 pieces (db, models,
mirror, sandbox, conversation, floor, moderator). `app.state` carries the
dependency-injection factories (`api/deps.py`) and the in-process session registries
(`api/runtime.py`'s `launch_runner` populates them) -- tests override the factories via
`app.dependency_overrides` to swap in fakes instead of the real `sbx`/Anthropic-backed
implementations, since this dev sandbox has neither (see docs/decisions.md).

M2.9's `lifespan` reconciles sandbox drift and relaunches any session that was mid-flight
when this process last stopped, before it starts serving requests. Startup must never
fail because of this: `reconcile_on_startup` itself tolerates `SandboxProvider.reconcile()`
failing (no `sbx` CLI, the same M2.4-flagged gap), and this lifespan ALSO wraps the whole
call -- e.g. an unreachable Postgres at boot must not prevent `/healthz` from ever coming
up. See docs/decisions.md.

ZERO AUTH in M2.8: no bearer tokens, no caller identity, no per-session ownership checks,
no rate limiting. Anyone who can reach this port can create tasks/sessions and
interject/stop on any session. Do not expose this service beyond a private/internal
network until M5 (human seat/auth) lands.
"""
import functools
import logging
import os
import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI

from acp import Envelope  # noqa: F401  (proves acp is a real dependency, kept from M1)

from .api import (agents_router, app_config_router, attachments_router, model_catalog_router, modes_router, pull_request_router,
                  sessions_router, tasks_router)
from .api.runtime import launch_runner
from .db import get_sessionmaker
from .floor.scorer import AnthropicHandRaiseScorer
from .llm import make_client
from .moderator.summarizer import AnthropicSummarizer
from .moderator.synthesizer import AnthropicSynthesizer
from .reconcile import reconcile_on_startup
from .sandbox.local import LocalSbxSandboxProvider
from .telemetry import configure_tracing

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_tracing()  # M2.10: must run here, not at import time -- see telemetry.py

    sessionmaker = get_sessionmaker()
    sandbox_provider = app.state.sandbox_provider_factory()

    missing = [v for v in ("BAYTO_SUMMARY_MODEL", "BAYTO_SYNTHESIS_MODEL") if not os.environ.get(v)]
    if missing:
        log.warning("not configured: %s unset -- starting a session will answer 503 until it is set "
                    "(see orchestrator/README.md, Configuration)", ", ".join(missing))

    relaunch_tasks: set[asyncio.Task] = set()

    async def _relaunch(session_id: uuid.UUID) -> None:
        """Start the relaunch in the background. Reconciliation runs before the API accepts
        requests, and a relaunch can take minutes (sandbox create, team start): awaiting it here
        kept the whole API returning 502 until it finished or timed out. launch_runner records its
        own failure on the session, so nothing is lost by not waiting."""
        task = asyncio.create_task(launch_runner(
            session_id, sessionmaker=sessionmaker, sandbox_provider=app.state.sandbox_provider_factory(),
            summarizer=app.state.summarizer_factory(), synthesizer=app.state.synthesizer_factory(),
            scorer_factory=app.state.scorer_factory,
            running_sessions=app.state.running_sessions, session_runtimes=app.state.session_runtimes,
        ))
        relaunch_tasks.add(task)  # strong reference: the loop only keeps weak ones
        task.add_done_callback(relaunch_tasks.discard)

    try:
        report = await reconcile_on_startup(sandbox_provider, sessionmaker, _relaunch)
        log.info("startup reconciliation: relaunched=%s skipped_orphaned=%s failed_to_launch=%s",
                 report.relaunched, report.skipped_orphaned, report.failed_to_launch)
    except Exception:
        log.exception("startup reconciliation failed entirely (e.g. Postgres unreachable at boot) "
                      "-- continuing to serve; no session was automatically relaunched this boot")
    yield
    close = getattr(sandbox_provider, "close", None)
    if close is not None:
        await close()  # releases held execs; the teams die with them


app = FastAPI(title="Bayto Orchestrator", lifespan=lifespan)

# In-process session registries (M2.8), populated by api/runtime.py's launch_runner.
app.state.running_sessions = {}  # session_id -> the ModeratorRunner's asyncio.Task
app.state.session_runtimes = {}  # session_id -> api.runtime.SessionRuntime

# Dependency-injection factories (api/deps.py reads these); tests replace them via
# app.dependency_overrides on the get_*() functions, not by mutating these directly.
# One provider for the whole process: it keeps the held `sbx exec` per sandbox (the team dies if its
# exec is released), so a fresh provider per request would lose track of every running team.
@functools.cache
def _local_sandbox_provider() -> LocalSbxSandboxProvider:
    from .sandbox.provision import provision_team_sandbox
    # BAYTO_PROVISION=0 falls back to a bare `sbx env create` (no team tools; for debugging only).
    provisioner = provision_team_sandbox if os.environ.get("BAYTO_PROVISION", "1") != "0" else None
    return LocalSbxSandboxProvider(get_sessionmaker(), provisioner=provisioner)


app.state.sandbox_provider_factory = _local_sandbox_provider
# The moderator's side calls use the Anthropic API when ANTHROPIC_API_KEY is set, otherwise the Claude
# subscription through `claude -p` (BAYTO_LLM_BACKEND=api|cli overrides). See orchestrator/llm.
app.state.summarizer_factory = lambda: AnthropicSummarizer(client=make_client())
app.state.synthesizer_factory = lambda: AnthropicSynthesizer(client=make_client())
app.state.scorer_factory = lambda: AnthropicHandRaiseScorer(client=make_client())

_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver", "test"}


@app.middleware("http")
async def _host_check(request, call_next):
    """Refuse requests whose Host is not local (DNS rebinding). BAYTO_ALLOWED_HOSTS adds names."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0] if not (request.headers.get("host") or "").startswith("[") \
        else (request.headers.get("host") or "").split("]")[0] + "]"
    extra = {h.strip() for h in os.environ.get("BAYTO_ALLOWED_HOSTS", "").split(",") if h.strip()}
    if host and host not in _ALLOWED_HOSTS and host not in extra:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": {"error": {"code": "bad_host", "detail": "unexpected Host header"}}}, status_code=400)
    return await call_next(request)


app.include_router(tasks_router)
app.include_router(attachments_router)
app.include_router(pull_request_router)
app.include_router(sessions_router)
app.include_router(modes_router)
app.include_router(agents_router)
app.include_router(model_catalog_router)
app.include_router(app_config_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
