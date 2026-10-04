"""Moderator-summary seam (M1.10 spike): a new, minimal Summarizer Protocol -- no
equivalent exists in this spike yet. Not the same thing as orchestrator's M2.7
AnthropicSummarizer, which is a *rolling* per-turn summary with a different call shape;
this is the one-shot final summary roles/coordinator.md step 5 requires (strongest point
per side, agreement, disagreement, recommendation) before `handoff stage finished`.

UNVERIFIED against the live Anthropic API -- same disclosure as scorer.py; see
FINDINGS.md for the compound gap this creates together with the missing `sbx` CLI.
"""
from __future__ import annotations

import os
from typing import Protocol

from anthropic import AsyncAnthropic
from pydantic import BaseModel

MODEL_ENV_VAR = "BAYTO_M1_DRIVER_SUMMARY_MODEL"
TOOL_NAME = "submit_moderator_summary"


class ModeratorSummary(BaseModel):
    strongest_points: dict[str, str]  # participant -> their strongest point
    agreement: str
    disagreement: str
    recommendation: str


class SummarizerError(RuntimeError):
    """The model's response didn't include the expected tool_use block."""


class Summarizer(Protocol):
    async def summarize(self, transcript: list[str]) -> ModeratorSummary: ...


def _tool_definition() -> dict:
    return {
        "name": TOOL_NAME,
        "description": ("Submit the final moderator summary of a converged debate: each "
                         "participant's strongest point, where they agree, where they "
                         "disagree, and a recommendation."),
        "input_schema": {
            "type": "object",
            "properties": {
                "strongest_points": {"type": "object", "additionalProperties": {"type": "string"}},
                "agreement": {"type": "string"},
                "disagreement": {"type": "string"},
                "recommendation": {"type": "string"},
            },
            "required": ["strongest_points", "agreement", "disagreement", "recommendation"],
        },
    }


def _user_content(transcript: list[str]) -> str:
    window = "\n".join(transcript) or "(no messages)"
    return f"Full transcript:\n{window}\n\nSubmit the final moderator summary."


def _parse_tool_use(response) -> ModeratorSummary:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == TOOL_NAME:
            return ModeratorSummary.model_validate(block.input)
    raise SummarizerError(f"no {TOOL_NAME!r} tool_use block in the response")


class AnthropicSummarizer:
    """Real impl. UNVERIFIED -- see module docstring."""

    def __init__(self, *, model: str | None = None, client: AsyncAnthropic | None = None) -> None:
        self._model = model or os.environ.get(MODEL_ENV_VAR)
        if not self._model:
            raise ValueError(f"no model id: pass model= or set {MODEL_ENV_VAR}")
        self._client = client or AsyncAnthropic()

    async def summarize(self, transcript: list[str]) -> ModeratorSummary:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system="You are the moderator delivering the final summary of a converged multi-agent debate.",
            messages=[{"role": "user", "content": _user_content(transcript)}],
            tools=[_tool_definition()],
            tool_choice={"type": "tool", "name": TOOL_NAME},
        )
        return _parse_tool_use(response)


class FakeSummarizer:
    """One canned ModeratorSummary -- no network."""

    def __init__(self, summary: ModeratorSummary) -> None:
        self._summary = summary

    async def summarize(self, transcript: list[str]) -> ModeratorSummary:
        return self._summary


def format_summary(summary: ModeratorSummary) -> str:
    lines = ["**Strongest points:**"]
    lines.extend(f"- {participant}: {point}" for participant, point in summary.strongest_points.items())
    lines.append("")
    lines.append(f"**Agreement:** {summary.agreement}")
    lines.append(f"**Disagreement:** {summary.disagreement}")
    lines.append(f"**Recommendation:** {summary.recommendation}")
    return "\n".join(lines)
