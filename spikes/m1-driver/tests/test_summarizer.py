"""M1.10 spike: AnthropicSummarizer is exercised only against a canned tool_use response
fixture -- no live Anthropic API call (same disclosure as test_scorer.py; see FINDINGS.md).
FakeSummarizer needs no fixture at all (covered in test_loop.py)."""
from types import SimpleNamespace

import pytest

from m1_driver.summarizer import TOOL_NAME, ModeratorSummary, SummarizerError, _parse_tool_use, format_summary


def canned_response(input_: dict):
    block = SimpleNamespace(type="tool_use", name=TOOL_NAME, input=input_)
    return SimpleNamespace(content=[block])


def test_parse_tool_use_extracts_a_valid_summary():
    response = canned_response({
        "strongest_points": {"advocate": "Ships faster."},
        "agreement": "Both want a staged rollout.",
        "disagreement": "Whether the risk is acceptable for v1.",
        "recommendation": "Ship behind a flag.",
    })
    result = _parse_tool_use(response)
    assert result == ModeratorSummary(
        strongest_points={"advocate": "Ships faster."},
        agreement="Both want a staged rollout.",
        disagreement="Whether the risk is acceptable for v1.",
        recommendation="Ship behind a flag.",
    )


def test_parse_tool_use_raises_when_no_matching_tool_use_block():
    response = SimpleNamespace(content=[SimpleNamespace(type="text", name=None, input=None)])
    with pytest.raises(SummarizerError):
        _parse_tool_use(response)


def test_format_summary_includes_every_field():
    summary = ModeratorSummary(
        strongest_points={"advocate": "Ships faster.", "skeptic": "Riskier rollback."},
        agreement="Both want a staged rollout.",
        disagreement="Whether the risk is acceptable for v1.",
        recommendation="Ship behind a flag.",
    )
    text = format_summary(summary)
    assert "advocate: Ships faster." in text
    assert "skeptic: Riskier rollback." in text
    assert "Both want a staged rollout." in text
    assert "Whether the risk is acceptable for v1." in text
    assert "Ship behind a flag." in text
