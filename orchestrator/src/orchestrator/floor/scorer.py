"""HandRaiseScorer: collects hand-raise signals for every seated persona (M2.6), via one
Haiku call scoring all personas rather than executing every sandbox each turn.

Genuinely async, not sync-wrapped-in-asyncio.to_thread: unlike SbxRunner/FileTransport
(sync-only libraries with no async option, per M2.4/M2.5), the Anthropic API has a real
async client, so the to_thread reasoning doesn't apply here.

UNVERIFIED against the live Anthropic API -- same situation as M2.4's missing `sbx` CLI:
this sandbox may not have ANTHROPIC_API_KEY reachable or open network egress. Verified
here only at the parsing/validation level, against a canned tool_use response fixture
(see tests/test_floor_scorer.py). A follow-up smoke-test task, gated on wherever the
orchestrator actually deploys, is needed before trusting AnthropicHandRaiseScorer against
a real call -- see docs/decisions.md, 2026-09-29.

The tool's JSON schema is read from agent-comms/schema/hand-raise.v1.json at runtime via
acp.floor.hand_raise_json_schema() (the single source of truth for both the wire shape and
this structured-output contract, nothing duplicated by hand) -- see that function's
docstring for the schema-packaging caveat this depends on.
"""
from __future__ import annotations

import os
from typing import Protocol

from acp.floor import HandRaise, hand_raise_json_schema
from anthropic import AsyncAnthropic
from opentelemetry import trace
from pydantic import BaseModel

from .types import ConversationView

MODEL_ENV_VAR = "BAYTO_HAND_RAISE_MODEL"
TOOL_NAME = "submit_hand_raises"

tracer = trace.get_tracer(__name__)


class PersonaBrief(BaseModel):
    """Orchestrator-only input struct, not wire protocol."""

    participant: str
    role: str
    stance: str | None
    brief: str


class ScorerError(RuntimeError):
    """The model's response didn't include the expected tool_use block."""


class HandRaiseScorer(Protocol):
    async def score(self, view: ConversationView, personas: list[PersonaBrief]) -> list[HandRaise]: ...


def _tool_definition() -> dict:
    return {
        "name": TOOL_NAME,
        "description": "Submit a hand-raise decision for every seated persona that wants the floor this round.",
        "input_schema": {
            "type": "object",
            "properties": {"hand_raises": {"type": "array", "items": hand_raise_json_schema()}},
            "required": ["hand_raises"],
        },
    }


def _system_prompt(personas: list[PersonaBrief]) -> str:
    lines = ["Score which seated personas want the floor this round.", ""]
    for p in personas:
        stance = f" ({p.stance})" if p.stance else ""
        lines.append(f"- {p.participant} [{p.role}{stance}]: {p.brief}")
    return "\n".join(lines)


def _user_content(view: ConversationView) -> str:
    window = "\n".join(view.recent_transcript) or "(no messages yet this round)"
    return f"Recent transcript:\n{window}\n\nSubmit a hand-raise (or skip) for each persona."


def _parse_tool_use(response) -> list[HandRaise]:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == TOOL_NAME:
            return [HandRaise.model_validate(item) for item in block.input["hand_raises"]]
    raise ScorerError(f"no {TOOL_NAME!r} tool_use block in the response")


class AnthropicHandRaiseScorer:
    def __init__(self, *, model: str | None = None, client: AsyncAnthropic | None = None) -> None:
        self._model = model or os.environ.get(MODEL_ENV_VAR)
        if not self._model:
            raise ValueError(f"no model id: pass model= or set {MODEL_ENV_VAR}")
        self._client = client or AsyncAnthropic()

    async def score(self, view: ConversationView, personas: list[PersonaBrief]) -> list[HandRaise]:
        with tracer.start_as_current_span(
            "floor.score", attributes={"bayto.session_id": str(view.session_id)}
        ) as span:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=_system_prompt(personas),
                messages=[{"role": "user", "content": _user_content(view)}],
                tools=[_tool_definition()],
                tool_choice={"type": "tool", "name": TOOL_NAME},
            )
            # Real usage from the Anthropic API response -- reliable, unlike a sandboxed
            # participant's self-reported (and possibly absent) turn meta.
            span.set_attribute("gen_ai.request.model", self._model)
            if getattr(response, "usage", None) is not None:
                span.set_attribute("gen_ai.usage.input_tokens", response.usage.input_tokens)
                span.set_attribute("gen_ai.usage.output_tokens", response.usage.output_tokens)
            return _parse_tool_use(response)


class FakeHandRaiseScorer:
    """Canned list[HandRaise], no network -- for M2.7's turn-runner tests (not built yet;
    defined now since the interface is settled)."""

    def __init__(self, hand_raises: list[HandRaise]) -> None:
        self._hand_raises = hand_raises

    async def score(self, view: ConversationView, personas: list[PersonaBrief]) -> list[HandRaise]:
        return self._hand_raises
