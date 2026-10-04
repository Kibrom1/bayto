"""HandRaiseScorer seam (M1.10 spike): same Protocol shape as orchestrator's
floor/scorer.py (M2.6) -- score(view, personas) -> list[HandRaise] -- but defined locally
so this spike has no dependency on orchestrator/ (see loop.py's module docstring).
ConversationView here is a deliberately minimal stand-in for orchestrator's richer
floor/types.ConversationView: just enough (round, recent_transcript) for a Haiku call to
score hand-raises against -- none of M2's turn-tracking/mute/grant-cap state, since this
spike's convergence rule (loop.py) doesn't use any of it.

UNVERIFIED against the live Anthropic API -- no ANTHROPIC_API_KEY/network egress assumed
available in this dev-team sandbox, same situation as M2.6's AnthropicHandRaiseScorer and
M2.4's missing `sbx` CLI (a compound gap; see FINDINGS.md). Verified here only at the
parsing/validation level against a canned tool_use fixture (tests/test_scorer.py).
"""
from __future__ import annotations

import os
from typing import Protocol

from acp.floor import HandRaise, hand_raise_json_schema
from anthropic import AsyncAnthropic
from pydantic import BaseModel, Field

MODEL_ENV_VAR = "BAYTO_M1_DRIVER_HAND_RAISE_MODEL"
TOOL_NAME = "submit_hand_raises"


class PersonaBrief(BaseModel):
    participant: str
    role: str
    stance: str | None = None
    brief: str


class ConversationView(BaseModel):
    round: int
    recent_transcript: list[str] = Field(default_factory=list)


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
    """Real impl. UNVERIFIED -- see module docstring."""

    def __init__(self, *, model: str | None = None, client: AsyncAnthropic | None = None) -> None:
        self._model = model or os.environ.get(MODEL_ENV_VAR)
        if not self._model:
            raise ValueError(f"no model id: pass model= or set {MODEL_ENV_VAR}")
        self._client = client or AsyncAnthropic()

    async def score(self, view: ConversationView, personas: list[PersonaBrief]) -> list[HandRaise]:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=_system_prompt(personas),
            messages=[{"role": "user", "content": _user_content(view)}],
            tools=[_tool_definition()],
            tool_choice={"type": "tool", "name": TOOL_NAME},
        )
        return _parse_tool_use(response)


class FakeHandRaiseScorer:
    """Canned list[HandRaise] per call, in order -- for loop convergence tests. Raises
    IndexError if exhausted (a test-setup bug, not something to silently paper over),
    same discipline as orchestrator's FakeSummarizer."""

    def __init__(self, hand_raises_by_round: list[list[HandRaise]]) -> None:
        self._rounds = hand_raises_by_round
        self._i = 0

    async def score(self, view: ConversationView, personas: list[PersonaBrief]) -> list[HandRaise]:
        result = self._rounds[self._i]
        self._i += 1
        return result
