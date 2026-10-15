import json
import os
import stat
import uuid

import pytest

from orchestrator.llm import ClaudeCliClient, ClaudeCliError, make_client
from orchestrator.llm.backend import backend_name
from orchestrator.llm.claude_cli import extract_json_object
from orchestrator.moderator.summarizer import AnthropicSummarizer
from orchestrator.moderator.turn_summary import TurnSummary


def _fake_claude(tmp_path, body: str):
    p = tmp_path / "claude"
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def _envelope(result: str, **kw) -> str:
    return json.dumps({"is_error": False, "result": result,
                       "usage": {"input_tokens": 11, "output_tokens": 7, "cache_read_input_tokens": 4}, **kw})


def test_extract_json_object_handles_fences_and_chatter():
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('Sure! {"a": {"b": 2}} done') == {"a": {"b": 2}}
    with pytest.raises(ClaudeCliError):
        extract_json_object("no json here")


def test_backend_auto_selects_cli_without_a_key(monkeypatch):
    monkeypatch.delenv("BAYTO_LLM_BACKEND", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert backend_name() == "cli" and isinstance(make_client(), ClaudeCliClient)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert backend_name() == "api" and make_client() is None
    monkeypatch.setenv("BAYTO_LLM_BACKEND", "cli")
    assert backend_name() == "cli"


async def test_summarizer_works_through_the_cli_client(tmp_path, monkeypatch):
    out = tmp_path / "out.json"
    out.write_text(_envelope('```json\n{"summary": "S", "has_new_argument": true}\n```'))
    argv_log = tmp_path / "argv.txt"
    binary = _fake_claude(tmp_path, f'printf "%s\\n" "$@" > {argv_log}\nenv | grep -c ANTHROPIC_API_KEY >> {argv_log}\ncat {out}\n')
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    summarizer = AnthropicSummarizer(model="claude-haiku-4-5", client=ClaudeCliClient(binary=binary))

    result = await summarizer.summarize(uuid.uuid4(), None, [TurnSummary(round=1, speaker="PM", content="hello")])

    assert result.summary == "S" and result.has_new_argument is True
    argv = argv_log.read_text().splitlines()
    assert "-p" in argv and "--model" in argv and "claude-haiku-4-5" in argv
    assert "--output-format" in argv and "json" in argv
    assert argv[-1] == "0"  # ANTHROPIC_API_KEY was stripped from the child environment


async def test_usage_is_reported(tmp_path):
    out = tmp_path / "o.json"
    out.write_text(_envelope('{"x": 1}'))
    client = ClaudeCliClient(binary=_fake_claude(tmp_path, f"cat {out}\n"))
    tool = {"name": "t", "input_schema": {"type": "object"}}
    resp = await client.messages.create(model="m", max_tokens=10, system="s",
                                        messages=[{"role": "user", "content": "q"}], tools=[tool],
                                        tool_choice={"type": "tool", "name": "t"})
    assert resp.content[0].input == {"x": 1}
    assert (resp.usage.input_tokens, resp.usage.output_tokens) == (15, 7)


async def test_retries_once_then_raises_on_non_json(tmp_path):
    out = tmp_path / "o.json"
    out.write_text(_envelope("I cannot do that"))
    client = ClaudeCliClient(binary=_fake_claude(tmp_path, f"cat {out}\n"))
    tool = {"name": "t", "input_schema": {"type": "object"}}
    with pytest.raises(ClaudeCliError, match="after retry"):
        await client.messages.create(model="m", max_tokens=1, system="s",
                                     messages=[{"role": "user", "content": "q"}], tools=[tool])


async def test_nonzero_exit_and_missing_binary_give_clear_errors(tmp_path):
    bad = ClaudeCliClient(binary=_fake_claude(tmp_path, "echo 'not logged in' >&2\nexit 1\n"))
    with pytest.raises(ClaudeCliError, match="not logged in"):
        await bad.run(model="m", system="s", prompt="p")
    with pytest.raises(ClaudeCliError, match="not found"):
        await ClaudeCliClient(binary="definitely-not-a-binary").run(model="m", system="s", prompt="p")


# ---------------------------------------------------------------- schema validation of replies

from orchestrator.llm.claude_cli import schema_errors

SUMMARY_TOOL = {"name": "t", "input_schema": {
    "type": "object",
    "properties": {"summary": {"type": "string"}, "has_new_argument": {"type": "boolean"},
                   "ids": {"type": "array", "items": {"type": "string"}}, "kind": {"enum": ["a", "b"]}},
    "required": ["summary", "has_new_argument"]}}


@pytest.mark.parametrize("value, fragment", [
    ({"summary": "s"}, "missing required field 'has_new_argument'"),
    ({"summary": 3, "has_new_argument": True}, "$.summary: expected string"),
    ({"summary": "s", "has_new_argument": "yes"}, "$.has_new_argument: expected boolean"),
    ({"summary": "s", "has_new_argument": True, "ids": ["a", 2]}, "$.ids[1]: expected string"),
    ({"summary": "s", "has_new_argument": True, "kind": "z"}, "must be one of"),
])
def test_schema_errors_names_the_problem(value, fragment):
    assert any(fragment in e for e in schema_errors(value, SUMMARY_TOOL["input_schema"]))


def test_schema_errors_accepts_a_valid_reply_and_does_not_treat_bool_as_number():
    assert schema_errors({"summary": "s", "has_new_argument": False, "ids": []}, SUMMARY_TOOL["input_schema"]) == []
    assert schema_errors(True, {"type": "integer"})


async def test_a_wrong_shape_reply_is_retried_with_the_problem_stated(tmp_path):
    """Valid JSON of the wrong shape used to be returned as-is and blew up later in the runner."""
    bad = tmp_path / "bad.json"
    good = tmp_path / "good.json"
    bad.write_text(_envelope('{"summary": "only this"}'))
    good.write_text(_envelope('{"summary": "S", "has_new_argument": true}'))
    seen = tmp_path / "prompts.txt"
    # First call answers badly; the second (whose prompt now names the problem) answers correctly.
    script = (f'printf "%s\\n---\\n" "$2" >> {seen}\n'
              f'case "$2" in *rejected*) cat {good};; *) cat {bad};; esac\n')
    client = ClaudeCliClient(binary=_fake_claude(tmp_path, script))
    resp = await client.messages.create(model="m", max_tokens=1, system="s",
                                        messages=[{"role": "user", "content": "q"}], tools=[SUMMARY_TOOL])
    assert resp.content[0].input == {"summary": "S", "has_new_argument": True}
    assert "missing required field 'has_new_argument'" in seen.read_text()


async def test_a_reply_that_stays_the_wrong_shape_fails_clearly(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(_envelope('{"summary": 5}'))
    client = ClaudeCliClient(binary=_fake_claude(tmp_path, f"cat {bad}\n"))
    with pytest.raises(ClaudeCliError, match="does not match the schema.*after retry"):
        await client.messages.create(model="m", max_tokens=1, system="s",
                                     messages=[{"role": "user", "content": "q"}], tools=[SUMMARY_TOOL])


# ---------------------------------------------------------------- failure detail

from orchestrator.llm.claude_cli import failure_detail


def test_failure_detail_surfaces_the_reason_buried_in_the_json_envelope():
    out = '{"duration_api_ms":0,"stop_reason":"stop_sequence","total_cost_usd":0,"usage":{"input_tokens":0},"is_error":true,"result":"Invalid API key \u00b7 Please run /login"}'
    assert failure_detail(out, "") == "Invalid API key \u00b7 Please run /login"


def test_failure_detail_prefers_stderr_and_falls_back_to_raw_text():
    assert failure_detail("{}", "not logged in\n") == "not logged in"
    assert failure_detail("plain text failure", "") == "plain text failure"


async def test_a_failing_claude_reports_the_reason_not_the_usage_block(tmp_path):
    out = tmp_path / "o.json"
    out.write_text('{"usage":{"input_tokens":0},"is_error":true,"result":"model not found: bogus"}')
    client = ClaudeCliClient(binary=_fake_claude(tmp_path, f"cat {out}\nexit 1\n"))
    with pytest.raises(ClaudeCliError, match="model not found: bogus"):
        await client.run(model="bogus", system="s", prompt="p")


# ---------------------------------------------------------------- the real tool schemas (regression: list-valued "type")

def _real_tools():
    from orchestrator.floor import scorer
    from orchestrator.moderator import summarizer, synthesizer
    return {"summarizer": summarizer._tool_definition(), "synthesizer": synthesizer._tool_definition(),
            "scorer": scorer._tool_definition()}


@pytest.mark.parametrize("name", ["summarizer", "synthesizer", "scorer"])
def test_the_validator_handles_every_real_tool_schema_without_raising(name):
    schema = _real_tools()[name]["input_schema"]
    problems = schema_errors({}, schema)  # used to raise TypeError on `"type": ["object", "null"]`
    assert any("missing required field" in p for p in problems)


def test_type_lists_and_null_are_accepted():
    schema = _real_tools()["synthesizer"]["input_schema"]
    base = {"content_json": {"a": 1}, "source_turn_ids": []}
    assert schema_errors({**base, "minority_report": None}, schema) == []
    assert schema_errors({**base, "minority_report": {"objection": "x"}}, schema) == []
    assert schema_errors({**base, "minority_report": "text"}, schema)  # a string is neither object nor null


def test_empty_hand_raises_is_a_valid_scorer_reply():
    assert schema_errors({"hand_raises": []}, _real_tools()["scorer"]["input_schema"]) == []
