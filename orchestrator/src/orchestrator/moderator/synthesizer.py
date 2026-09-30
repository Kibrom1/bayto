"""Synthesizer: final synthesis into an artifact, plus an optional minority report (M2.7).
Larger model tier per product-design.md's cost-routing split ("larger models only for
substantive turns and final synthesis") -- the tier choice is doc-given; the exact model
string is still a config value (MODEL_ENV_VAR), never hardcoded.

Minority report -- product-owner's confirmed acceptance criteria: 0 or 1
type="minority_report" Artifact row per session, NEVER one per dissenting agent; if
multiple agents dissent for the same underlying reason, consolidate into that single row.
Lead with the dissenting agent's specific objection, grounded in the transcript; include an
alternate recommendation ONLY if the transcript actually surfaces one -- never invent one.
This criteria lives entirely in `_tool_definition()`'s schema description below (a
content/judgment question for the model), not in ModeratorRunner's control-flow code:
the runner only ever checks "is `minority_report` None or not" to decide whether to insert
a second Artifact row (see runner.py's `_finish`).

Same seam philosophy as summarizer.py/M2.6's scorer.py (real impl + fake for tests).
UNVERIFIED against the live Anthropic API for the same disclosed reason -- see
docs/decisions.md, 2026-09-29/30. In particular, the minority-report *judgment itself*
(does prompting actually produce zero rows for a consensus transcript and exactly one for a
late-holdout transcript) can only be verified with a real model call; the acceptance tests
here (test_moderator_synthesis.py) instead verify the *mechanical* consumption of
SynthesisResult.minority_report via FakeSynthesizer -- that ModeratorRunner creates 0 or 1
Artifact rows exactly matching what the synthesizer returned, not that a real LLM call
would return the right judgment for a given transcript.
"""
from __future__ import annotations

import os
import uuid
from typing import Protocol

from anthropic import AsyncAnthropic
from opentelemetry import trace
from pydantic import BaseModel

from ..floor import ConversationView
from .turn_summary import TurnSummary

MODEL_ENV_VAR = "BAYTO_SYNTHESIS_MODEL"
TOOL_NAME = "submit_synthesis"

tracer = trace.get_tracer(__name__)


class SynthesisResult(BaseModel):
    content_json: dict
    source_turn_ids: list[uuid.UUID]
    minority_report: dict | None = None


class Synthesizer(Protocol):
    async def synthesize(self, view: ConversationView, summary: str,
                          all_turns: list[TurnSummary]) -> SynthesisResult: ...


class SynthesizerError(RuntimeError):
    """The model's response didn't include the expected tool_use block."""


def _tool_definition() -> dict:
    return {
        "name": TOOL_NAME,
        "description": ("Submit the final synthesis artifact for this session, and a minority report "
                         "only if a clear late dissent actually appears in the transcript."),
        "input_schema": {
            "type": "object",
            "properties": {
                "content_json": {"type": "object", "description": "The synthesis artifact's content."},
                "source_turn_ids": {"type": "array", "items": {"type": "string", "format": "uuid"}},
                "minority_report": {
                    "type": ["object", "null"],
                    "description": (
                        "A single consolidated minority report if, and only if, at least one agent's "
                        "position in the final 1-2 turns before stop/convergence clearly diverges from "
                        "this synthesis; null otherwise. Never one row per dissenting agent -- if several "
                        "agents dissent for the same underlying reason, consolidate them into this one "
                        "report. Lead with the dissenting agent's specific objection (what they disagreed "
                        "with and why), grounded in the transcript. Include an alternate recommendation "
                        "only if the transcript actually surfaces one -- do not invent one that wasn't "
                        "argued."
                    ),
                },
            },
            "required": ["content_json", "source_turn_ids"],
        },
    }


def _user_content(view: ConversationView, summary: str, all_turns: list[TurnSummary]) -> str:
    lines = [f"Rolling summary: {summary or '(none)'}", "", "Full transcript:"]
    lines.extend(f"[round {t.round}] {t.speaker}: {t.content}" for t in all_turns)
    lines.append("")
    lines.append("Submit the final synthesis (and a minority report only if the criteria above are met).")
    return "\n".join(lines)


def _parse_tool_use(response) -> SynthesisResult:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == TOOL_NAME:
            return SynthesisResult.model_validate(block.input)
    raise SynthesizerError(f"no {TOOL_NAME!r} tool_use block in the response")


class AnthropicSynthesizer:
    def __init__(self, *, model: str | None = None, client: AsyncAnthropic | None = None) -> None:
        self._model = model or os.environ.get(MODEL_ENV_VAR)
        if not self._model:
            raise ValueError(f"no model id: pass model= or set {MODEL_ENV_VAR}")
        self._client = client or AsyncAnthropic()

    async def synthesize(self, view: ConversationView, summary: str,
                          all_turns: list[TurnSummary]) -> SynthesisResult:
        with tracer.start_as_current_span(
            "moderator.synthesize", attributes={"bayto.session_id": str(view.session_id)}
        ) as span:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system="You write the final synthesis artifact for a multi-agent conversation.",
                messages=[{"role": "user", "content": _user_content(view, summary, all_turns)}],
                tools=[_tool_definition()],
                tool_choice={"type": "tool", "name": TOOL_NAME},
            )
            span.set_attribute("gen_ai.request.model", self._model)
            if getattr(response, "usage", None) is not None:
                span.set_attribute("gen_ai.usage.input_tokens", response.usage.input_tokens)
                span.set_attribute("gen_ai.usage.output_tokens", response.usage.output_tokens)
            return _parse_tool_use(response)


class FakeSynthesizer:
    """Canned SynthesisResult, no network."""

    def __init__(self, result: SynthesisResult) -> None:
        self._result = result

    async def synthesize(self, view: ConversationView, summary: str,
                          all_turns: list[TurnSummary]) -> SynthesisResult:
        return self._result
