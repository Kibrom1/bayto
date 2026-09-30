"""M2.6: the HandRaise-scoring seam. AnthropicHandRaiseScorer is exercised only against a
canned tool_use response fixture (no live Anthropic API call -- no credentials or network
egress assumed available here; see scorer.py's module docstring and docs/decisions.md).
FakeHandRaiseScorer needs no fixture at all."""
import uuid
from types import SimpleNamespace

import pytest
from acp.floor import HandRaise, Reason, hand_raise_json_schema
from orchestrator.floor import ConversationView, PersonaBrief
from orchestrator.floor.scorer import (
    MODEL_ENV_VAR,
    TOOL_NAME,
    AnthropicHandRaiseScorer,
    FakeHandRaiseScorer,
    ScorerError,
    _parse_tool_use,
    _tool_definition,
)

SESSION = uuid.uuid4()


def mk_view(**kw) -> ConversationView:
    defaults = dict(session_id=SESSION, round=1, seat_order=["a", "b"])
    defaults.update(kw)
    return ConversationView(**defaults)


def canned_response(hand_raises: list[dict]):
    """A minimal stand-in for anthropic.types.Message -- _parse_tool_use only ever reads
    .content[i].type/.name/.input, duck-typed so no real SDK response object is needed."""
    block = SimpleNamespace(type="tool_use", name=TOOL_NAME, input={"hand_raises": hand_raises})
    return SimpleNamespace(content=[block])


# ---------------------------------------------------------------- parsing

def test_parse_tool_use_extracts_valid_hand_raises():
    response = canned_response([
        {"participant": "advocate", "reason": "disagree", "urgency": 0.6, "in_reply_to": "m1"},
        {"participant": "skeptic", "reason": "agree_pass", "urgency": 0.1},
    ])
    result = _parse_tool_use(response)
    assert result == [
        HandRaise(participant="advocate", reason=Reason.DISAGREE, urgency=0.6, in_reply_to="m1"),
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


def test_parse_tool_use_raises_on_a_malformed_hand_raise():
    response = canned_response([{"participant": "a", "reason": "not-a-real-reason", "urgency": 0.5}])
    with pytest.raises(Exception):
        _parse_tool_use(response)


# ---------------------------------------------------------------- tool schema

def test_tool_definition_wraps_the_real_hand_raise_schema_verbatim():
    tool = _tool_definition()
    assert tool["name"] == TOOL_NAME
    assert tool["input_schema"]["properties"]["hand_raises"]["items"] == hand_raise_json_schema()
    assert tool["input_schema"]["required"] == ["hand_raises"]


# ---------------------------------------------------------------- AnthropicHandRaiseScorer wiring

class FakeMessages:
    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class FakeAnthropicClient:
    def __init__(self, response):
        self.messages = FakeMessages(response)


async def test_score_sends_the_forced_tool_choice_and_parses_the_response():
    response = canned_response([{"participant": "a", "reason": "new_point", "urgency": 0.7}])
    client = FakeAnthropicClient(response)
    scorer = AnthropicHandRaiseScorer(model="claude-haiku-fake", client=client)

    personas = [PersonaBrief(participant="a", role="Architect", stance=None, brief="reviews designs")]
    result = await scorer.score(mk_view(), personas)

    assert result == [HandRaise(participant="a", reason=Reason.NEW_POINT, urgency=0.7)]
    [call] = client.messages.calls
    assert call["model"] == "claude-haiku-fake"
    assert call["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert call["tools"][0]["name"] == TOOL_NAME


# ---------------------------------------------------------------- M2.10 OTel spans

async def test_score_creates_a_floor_score_span_with_real_usage_attributes(span_exporter):
    response = canned_response([{"participant": "a", "reason": "new_point", "urgency": 0.7}])
    response.usage = SimpleNamespace(input_tokens=123, output_tokens=45)
    client = FakeAnthropicClient(response)
    scorer = AnthropicHandRaiseScorer(model="claude-haiku-fake", client=client)

    await scorer.score(mk_view(), [PersonaBrief(participant="a", role="r", stance=None, brief="b")])

    [span] = span_exporter.get_finished_spans()
    assert span.name == "floor.score"
    assert span.attributes["bayto.session_id"] == str(SESSION)
    assert span.attributes["gen_ai.request.model"] == "claude-haiku-fake"
    assert span.attributes["gen_ai.usage.input_tokens"] == 123
    assert span.attributes["gen_ai.usage.output_tokens"] == 45
    assert span.end_time >= span.start_time


async def test_score_span_omits_usage_attributes_when_the_response_has_none(span_exporter):
    """No usage field on the canned response (the common shape test fixtures already use)
    -- the span must omit these attributes entirely, not report them as 0 or None."""
    response = canned_response([])
    client = FakeAnthropicClient(response)
    scorer = AnthropicHandRaiseScorer(model="claude-haiku-fake", client=client)

    await scorer.score(mk_view(), [])

    [span] = span_exporter.get_finished_spans()
    assert "gen_ai.usage.input_tokens" not in span.attributes
    assert "gen_ai.usage.output_tokens" not in span.attributes


async def test_fake_scorer_creates_no_spans(span_exporter):
    """Instrumenting fakes would just pollute test runs with meaningless data."""
    scorer = FakeHandRaiseScorer([])
    await scorer.score(mk_view(), [])
    assert span_exporter.get_finished_spans() == ()


def test_requires_a_model_from_either_the_constructor_or_the_env_var(monkeypatch):
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    with pytest.raises(ValueError):
        AnthropicHandRaiseScorer(client=FakeAnthropicClient(canned_response([])))


def test_model_can_come_from_the_env_var(monkeypatch):
    monkeypatch.setenv(MODEL_ENV_VAR, "claude-haiku-from-env")
    scorer = AnthropicHandRaiseScorer(client=FakeAnthropicClient(canned_response([])))
    assert scorer._model == "claude-haiku-from-env"


# ---------------------------------------------------------------- FakeHandRaiseScorer

async def test_fake_scorer_returns_its_canned_list_regardless_of_input():
    canned = [HandRaise(participant="a", reason=Reason.NEW_POINT, urgency=0.5)]
    scorer = FakeHandRaiseScorer(canned)
    result = await scorer.score(mk_view(), personas=[])
    assert result == canned
    assert result is canned
