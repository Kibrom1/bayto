"""M2.7's AnthropicSynthesizer request/response parsing (never had its own test file until
M2.10's OTel span work needed to exercise it directly) + M2.10's `moderator.synthesize`
span. No live Anthropic API call needed -- the injectable client is a fake."""
import uuid
from types import SimpleNamespace

import pytest
from orchestrator.floor import ConversationView
from orchestrator.moderator.synthesizer import (
    MODEL_ENV_VAR,
    TOOL_NAME,
    AnthropicSynthesizer,
    FakeSynthesizer,
    SynthesisResult,
    SynthesizerError,
    _parse_tool_use,
)
from orchestrator.moderator.turn_summary import TurnSummary

SESSION = uuid.uuid4()


def mk_view() -> ConversationView:
    return ConversationView(session_id=SESSION, round=1, seat_order=["a"])


def canned_response(content_json: dict, source_turn_ids: list, minority_report=None):
    block = SimpleNamespace(type="tool_use", name=TOOL_NAME, input={
        "content_json": content_json, "source_turn_ids": [str(i) for i in source_turn_ids],
        "minority_report": minority_report,
    })
    return SimpleNamespace(content=[block])


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


def test_parse_tool_use_extracts_the_synthesis_result():
    turn_id = uuid.uuid4()
    response = canned_response({"summary": "done"}, [turn_id])
    assert _parse_tool_use(response) == SynthesisResult(content_json={"summary": "done"},
                                                          source_turn_ids=[turn_id])


def test_parse_tool_use_extracts_a_minority_report_when_present():
    response = canned_response({"summary": "done"}, [], minority_report={"objection": "x"})
    result = _parse_tool_use(response)
    assert result.minority_report == {"objection": "x"}


def test_parse_tool_use_raises_when_no_matching_tool_use_block():
    response = SimpleNamespace(content=[SimpleNamespace(type="text", name=None, input=None)])
    with pytest.raises(SynthesizerError):
        _parse_tool_use(response)


async def test_synthesize_sends_the_forced_tool_choice_and_parses_the_response():
    response = canned_response({}, [])
    client = FakeAnthropicClient(response)
    synthesizer = AnthropicSynthesizer(model="claude-opus-fake", client=client)

    result = await synthesizer.synthesize(mk_view(), "summary", [TurnSummary(speaker="a", content="hi", round=1)])

    assert result == SynthesisResult(content_json={}, source_turn_ids=[])
    [call] = client.messages.calls
    assert call["model"] == "claude-opus-fake"
    assert call["tool_choice"] == {"type": "tool", "name": TOOL_NAME}


def test_requires_a_model_from_either_the_constructor_or_the_env_var(monkeypatch):
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    with pytest.raises(ValueError):
        AnthropicSynthesizer(client=FakeAnthropicClient(canned_response({}, [])))


# ---------------------------------------------------------------- M2.10 OTel spans

async def test_synthesize_creates_a_span_with_real_usage_attributes(span_exporter):
    response = canned_response({}, [])
    response.usage = SimpleNamespace(input_tokens=200, output_tokens=300)
    synthesizer = AnthropicSynthesizer(model="claude-opus-fake", client=FakeAnthropicClient(response))

    await synthesizer.synthesize(mk_view(), "s", [])

    [span] = span_exporter.get_finished_spans()
    assert span.name == "moderator.synthesize"
    assert span.attributes["bayto.session_id"] == str(SESSION)
    assert span.attributes["gen_ai.request.model"] == "claude-opus-fake"
    assert span.attributes["gen_ai.usage.input_tokens"] == 200
    assert span.attributes["gen_ai.usage.output_tokens"] == 300


async def test_synthesize_span_omits_usage_attributes_when_the_response_has_none(span_exporter):
    synthesizer = AnthropicSynthesizer(model="claude-opus-fake", client=FakeAnthropicClient(canned_response({}, [])))
    await synthesizer.synthesize(mk_view(), "s", [])
    [span] = span_exporter.get_finished_spans()
    assert "gen_ai.usage.input_tokens" not in span.attributes


async def test_fake_synthesizer_creates_no_spans(span_exporter):
    synthesizer = FakeSynthesizer(SynthesisResult(content_json={}, source_turn_ids=[]))
    await synthesizer.synthesize(mk_view(), "s", [])
    assert span_exporter.get_finished_spans() == ()
