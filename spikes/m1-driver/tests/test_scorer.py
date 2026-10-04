"""M1.10 spike: AnthropicHandRaiseScorer is exercised only against a canned tool_use
response fixture -- no live Anthropic API call (no credentials/network egress assumed
available here; see scorer.py's module docstring and FINDINGS.md). FakeHandRaiseScorer
needs no fixture at all (covered in test_loop.py)."""
from types import SimpleNamespace

import pytest
from acp.floor import HandRaise, Reason

from m1_driver.scorer import TOOL_NAME, ScorerError, _parse_tool_use


def canned_response(hand_raises: list[dict]):
    block = SimpleNamespace(type="tool_use", name=TOOL_NAME, input={"hand_raises": hand_raises})
    return SimpleNamespace(content=[block])


def test_parse_tool_use_extracts_valid_hand_raises():
    response = canned_response([
        {"participant": "advocate", "reason": "new_point", "urgency": 0.6},
        {"participant": "skeptic", "reason": "agree_pass", "urgency": 0.1},
    ])
    assert _parse_tool_use(response) == [
        HandRaise(participant="advocate", reason=Reason.NEW_POINT, urgency=0.6),
        HandRaise(participant="skeptic", reason=Reason.AGREE_PASS, urgency=0.1),
    ]


def test_parse_tool_use_ignores_non_tool_use_blocks_before_the_real_one():
    text_block = SimpleNamespace(type="text", name=None, input=None)
    tool_block = SimpleNamespace(type="tool_use", name=TOOL_NAME, input={"hand_raises": []})
    response = SimpleNamespace(content=[text_block, tool_block])
    assert _parse_tool_use(response) == []


def test_parse_tool_use_raises_when_no_matching_tool_use_block():
    response = SimpleNamespace(content=[SimpleNamespace(type="text", name=None, input=None)])
    with pytest.raises(ScorerError):
        _parse_tool_use(response)
