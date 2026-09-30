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

from fastapi import Request
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


def get_summarizer(request: Request) -> Summarizer:
    return request.app.state.summarizer_factory()


def get_synthesizer(request: Request) -> Synthesizer:
    return request.app.state.synthesizer_factory()


def get_hand_raise_scorer_factory(request: Request) -> Callable[[], HandRaiseScorer]:
    return request.app.state.scorer_factory
