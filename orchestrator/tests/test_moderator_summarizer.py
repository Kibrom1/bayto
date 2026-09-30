"""M2.7's AnthropicSummarizer request/response parsing (never had its own test file until
M2.10's OTel span work needed to exercise it directly) + M2.10's `moderator.summarize`
span. No live Anthropic API call needed -- the injectable client is a fake."""
import uuid
from types import SimpleNamespace

import pytest
from orchestrator.moderator.summarizer import (
    MODEL_ENV_VAR,
    TOOL_NAME,
    AnthropicSummarizer,
    FakeSummarizer,
    SummarizerError,
    SummaryResult,
    _parse_tool_use,
)
from orchestrator.moderator.turn_summary import TurnSummary

SESSION = uuid.uuid4()


def canned_response(summary: str, has_new_argument: bool):
    block = SimpleNamespace(type="tool_use", name=TOOL_NAME,
                             input={"summary": summary, "has_new_argument": has_new_argument})
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


def test_parse_tool_use_extracts_the_summary_result():
    response = canned_response("things happened", True)
    assert _parse_tool_use(response) == SummaryResult(summary="things happened", has_new_argument=True)


def test_parse_tool_use_raises_when_no_matching_tool_use_block():
    response = SimpleNamespace(content=[SimpleNamespace(type="text", name=None, input=None)])
    with pytest.raises(SummarizerError):
        _parse_tool_use(response)


async def test_summarize_sends_the_forced_tool_choice_and_parses_the_response():
    response = canned_response("s", False)
    client = FakeAnthropicClient(response)
    summarizer = AnthropicSummarizer(model="claude-haiku-fake", client=client)

    result = await summarizer.summarize(SESSION, "prev", [TurnSummary(speaker="a", content="hi", round=1)])

    assert result == SummaryResult(summary="s", has_new_argument=False)
    [call] = client.messages.calls
    assert call["model"] == "claude-haiku-fake"
    assert call["tool_choice"] == {"type": "tool", "name": TOOL_NAME}


def test_requires_a_model_from_either_the_constructor_or_the_env_var(monkeypatch):
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    with pytest.raises(ValueError):
        AnthropicSummarizer(client=FakeAnthropicClient(canned_response("s", True)))


# ---------------------------------------------------------------- M2.10 OTel spans

async def test_summarize_creates_a_span_with_real_usage_attributes(span_exporter):
    response = canned_response("s", True)
    response.usage = SimpleNamespace(input_tokens=10, output_tokens=20)
    summarizer = AnthropicSummarizer(model="claude-haiku-fake", client=FakeAnthropicClient(response))

    await summarizer.summarize(SESSION, None, [TurnSummary(speaker="a", content="hi", round=1)])

    [span] = span_exporter.get_finished_spans()
    assert span.name == "moderator.summarize"
    assert span.attributes["bayto.session_id"] == str(SESSION)
    assert span.attributes["gen_ai.request.model"] == "claude-haiku-fake"
    assert span.attributes["gen_ai.usage.input_tokens"] == 10
    assert span.attributes["gen_ai.usage.output_tokens"] == 20


async def test_summarize_span_omits_usage_attributes_when_the_response_has_none(span_exporter):
    summarizer = AnthropicSummarizer(model="claude-haiku-fake", client=FakeAnthropicClient(canned_response("s", True)))
    await summarizer.summarize(SESSION, None, [])
    [span] = span_exporter.get_finished_spans()
    assert "gen_ai.usage.input_tokens" not in span.attributes


async def test_fake_summarizer_creates_no_spans(span_exporter):
    summarizer = FakeSummarizer([SummaryResult(summary="s", has_new_argument=True)])
    await summarizer.summarize(SESSION, None, [])
    assert span_exporter.get_finished_spans() == ()
