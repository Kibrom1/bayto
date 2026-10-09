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
