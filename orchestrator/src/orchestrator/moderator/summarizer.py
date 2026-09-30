"""Summarizer: a rolling summary of the transcript so far (M2.7), one Haiku call per
ModeratorRunner iteration rather than re-summarizing the entire transcript from scratch --
the whole reason it's persisted on Session (see models.py's Session.rolling_summary
docstring) instead of recomputed. Cheap model tier per product-design.md's cost-routing
split ("small models for floor signals, summaries and stop checks").

Same seam philosophy as M2.6's HandRaiseScorer (real impl + fake for tests) and a
genuinely async Protocol for the same reason (a real async Anthropic client exists, so no
to_thread wrapping) -- but a different call shape, so its own Protocol, not a shared one.
ModeratorRunner constructs one AsyncAnthropic client and injects it into the scorer,
summarizer and synthesizer alike.

UNVERIFIED against the live Anthropic API -- same disclosure as M2.6's
AnthropicHandRaiseScorer: no ANTHROPIC_API_KEY or network egress assumed available in this
dev-team sandbox. Verified only via a canned tool_use response fixture; see
docs/decisions.md, 2026-09-29/30.
"""
from __future__ import annotations

import os
import uuid
from typing import Protocol

from anthropic import AsyncAnthropic
from opentelemetry import trace
from pydantic import BaseModel

from .turn_summary import TurnSummary

MODEL_ENV_VAR = "BAYTO_SUMMARY_MODEL"
TOOL_NAME = "submit_summary"

tracer = trace.get_tracer(__name__)


class SummaryResult(BaseModel):
    summary: str
    has_new_argument: bool  # feeds the no-new-arguments-for-K-turns stop condition


class Summarizer(Protocol):
    async def summarize(self, session_id: uuid.UUID, previous_summary: str | None,
                         new_turns: list[TurnSummary]) -> SummaryResult: ...


class SummarizerError(RuntimeError):
    """The model's response didn't include the expected tool_use block."""


def _tool_definition() -> dict:
    return {
        "name": TOOL_NAME,
        "description": ("Submit an updated rolling summary of the conversation, and whether the new turns "
                         "raised a genuinely new argument (not just a restatement or agreement)."),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "has_new_argument": {"type": "boolean"},
            },
            "required": ["summary", "has_new_argument"],
        },
    }


def _user_content(previous_summary: str | None, new_turns: list[TurnSummary]) -> str:
    lines = [f"Previous summary: {previous_summary or '(none yet)'}", "", "New turns:"]
    lines.extend(f"[round {t.round}] {t.speaker}: {t.content}" for t in new_turns)
    lines.append("")
    lines.append("Submit an updated summary and whether these new turns raised a genuinely new "
                  "argument.")
    return "\n".join(lines)


def _parse_tool_use(response) -> SummaryResult:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == TOOL_NAME:
            return SummaryResult.model_validate(block.input)
    raise SummarizerError(f"no {TOOL_NAME!r} tool_use block in the response")


class AnthropicSummarizer:
    def __init__(self, *, model: str | None = None, client: AsyncAnthropic | None = None) -> None:
        self._model = model or os.environ.get(MODEL_ENV_VAR)
        if not self._model:
            raise ValueError(f"no model id: pass model= or set {MODEL_ENV_VAR}")
        self._client = client or AsyncAnthropic()

    async def summarize(self, session_id: uuid.UUID, previous_summary: str | None,
                         new_turns: list[TurnSummary]) -> SummaryResult:
        with tracer.start_as_current_span("moderator.summarize",
                                           attributes={"bayto.session_id": str(session_id)}) as span:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system="You maintain a rolling summary of a multi-agent conversation for context-window efficiency.",
                messages=[{"role": "user", "content": _user_content(previous_summary, new_turns)}],
                tools=[_tool_definition()],
                tool_choice={"type": "tool", "name": TOOL_NAME},
            )
            span.set_attribute("gen_ai.request.model", self._model)
            if getattr(response, "usage", None) is not None:
                span.set_attribute("gen_ai.usage.input_tokens", response.usage.input_tokens)
                span.set_attribute("gen_ai.usage.output_tokens", response.usage.output_tokens)
            return _parse_tool_use(response)


class FakeSummarizer:
    """Canned SummaryResults, no network -- one per call, in order; raises IndexError if
    exhausted (a test-setup bug, not something to silently paper over)."""

    def __init__(self, results: list[SummaryResult]) -> None:
        self._results = results
        self._i = 0

    async def summarize(self, session_id: uuid.UUID, previous_summary: str | None,
                         new_turns: list[TurnSummary]) -> SummaryResult:
        result = self._results[self._i]
        self._i += 1
        return result
