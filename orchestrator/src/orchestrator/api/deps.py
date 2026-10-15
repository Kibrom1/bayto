"""FastAPI dependency-injection seams for the REST API (M2.8).

`SandboxProvider` and the LLM seams (`Summarizer`/`Synthesizer`/`HandRaiseScorer`) are
swappable per the architect's testing strategy: tests override these via
`app.dependency_overrides` to inject M2.4's `FakeSandboxProvider`-style fakes and
M2.6/M2.7's `Fake*` seams instead of the real `sbx`/Anthropic-backed implementations --
this dev sandbox has neither a live `sbx` CLI nor live Anthropic credentials (the same
compound gap M2.4/M2.6/M2.7 already disclosed; see docs/decisions.md).

`get_hand_raise_scorer_factory` returns the *factory*, not an instance: a scorer is only
ever needed for raise-hand-mode sessions, and constructing `AnthropicHandRaiseScorer`
eagerly for every session (including round-robin ones) would require an API key even when
nothing will ever call it.
"""
from __future__ import annotations

from typing import Callable

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..db import get_sessionmaker
from ..floor.scorer import HandRaiseScorer
from ..moderator.summarizer import Summarizer
from ..moderator.synthesizer import Synthesizer
from ..sandbox.provider import SandboxProvider


def get_db_sessionmaker() -> async_sessionmaker:
    return get_sessionmaker()


def get_sandbox_provider(request: Request) -> SandboxProvider:
    return request.app.state.sandbox_provider_factory()


def _not_configured(exc: ValueError) -> HTTPException:
    """A missing BAYTO_*_MODEL variable is a deployment problem, not a server bug: say which one
    (the factory's message names it) with a 503 instead of an anonymous 500."""
    return HTTPException(503, detail={"error": {"code": "not_configured", "detail": str(exc)}})


def get_summarizer(request: Request) -> Summarizer:
    try:
        return request.app.state.summarizer_factory()
    except ValueError as exc:
        raise _not_configured(exc) from exc


def get_synthesizer(request: Request) -> Synthesizer:
    try:
        return request.app.state.synthesizer_factory()
    except ValueError as exc:
        raise _not_configured(exc) from exc


def get_hand_raise_scorer_factory(request: Request) -> Callable[[], HandRaiseScorer]:
    return request.app.state.scorer_factory


async def default_login_check() -> None:
    """Before a session starts or resumes: with the subscription CLI as the moderator backend, make
    sure the `claude` login still works, so an expired login fails here with a clear message instead
    of minutes later inside a turn. A no-op for the API backend."""
    from ..llm.backend import backend_name
    from ..llm.claude_cli import ClaudeCliError, check_login
    if backend_name() != "cli":
        return
    try:
        await check_login()
    except ClaudeCliError as exc:
        raise HTTPException(503, detail={"error": {
            "code": "moderator_login",
            "detail": f"The moderator cannot reach Claude: {exc}. Run `claude` in a terminal and use /login, then try again."}}) from exc


async def get_login_check(request: Request):
    return getattr(request.app.state, "login_check", default_login_check)
