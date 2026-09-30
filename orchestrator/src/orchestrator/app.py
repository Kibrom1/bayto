"""Bayto Orchestrator FastAPI app.

M2.8 mounts the task/session REST API on top of the M2.1-M2.7 pieces (db, models,
mirror, sandbox, conversation, floor, moderator). `app.state` carries the
dependency-injection factories (`api/deps.py`) and the in-process session registries
(`api/runtime.py`'s `launch_runner` populates them) -- tests override the factories via
`app.dependency_overrides` to swap in fakes instead of the real `sbx`/Anthropic-backed
implementations, since this dev sandbox has neither (see docs/decisions.md).

ZERO AUTH in M2.8: no bearer tokens, no caller identity, no per-session ownership checks,
no rate limiting. Anyone who can reach this port can create tasks/sessions and
interject/stop on any session. Do not expose this service beyond a private/internal
network until M5 (human seat/auth) lands.
"""
from fastapi import FastAPI

from acp import Envelope  # noqa: F401  (proves acp is a real dependency, kept from M1)

from .api import sessions_router, tasks_router
from .db import get_sessionmaker
from .floor.scorer import AnthropicHandRaiseScorer
from .moderator.summarizer import AnthropicSummarizer
from .moderator.synthesizer import AnthropicSynthesizer
from .sandbox.local import LocalSbxSandboxProvider

app = FastAPI(title="Bayto Orchestrator")

# In-process session registries (M2.8), populated by api/runtime.py's launch_runner.
app.state.running_sessions = {}  # session_id -> the ModeratorRunner's asyncio.Task
app.state.session_runtimes = {}  # session_id -> api.runtime.SessionRuntime

# Dependency-injection factories (api/deps.py reads these); tests replace them via
# app.dependency_overrides on the get_*() functions, not by mutating these directly.
app.state.sandbox_provider_factory = lambda: LocalSbxSandboxProvider(get_sessionmaker())
app.state.summarizer_factory = AnthropicSummarizer
app.state.synthesizer_factory = AnthropicSynthesizer
app.state.scorer_factory = AnthropicHandRaiseScorer

app.include_router(tasks_router)
app.include_router(sessions_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
