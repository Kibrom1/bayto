"""Detecting that Claude is out of usage. The payloads are written from what Claude Code is known to print; none was
captured from a real exhausted account (see docs/per-agent-models.md), which is why each shape is its own test."""
import json
import stat
from datetime import datetime, timedelta, timezone

import pytest

from orchestrator.limits import DEFAULT_RETRY_MINUTES, LimitHit, detect_limit, detect_limit_in_text, looks_like_platform_limit, parse_reset
from orchestrator.llm import ClaudeCliClient, ClaudeCliError
from orchestrator.sandbox.turns import TurnResult, parse_stream_line

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def test_a_result_with_is_error_and_a_limit_message_is_a_limit():
    hit = detect_limit({"type": "result", "subtype": "success", "is_error": True,
                        "result": "Claude AI usage limit reached|1792000000"}, now=NOW)
    assert hit and hit.source == "structured" and hit.reset_at == datetime.fromtimestamp(1792000000, tz=timezone.utc)


def test_newer_wording_with_a_named_zone_gives_a_reset_time():
    hit = detect_limit({"is_error": True, "result": "You've hit your limit · resets 5pm (America/New_York)"}, now=NOW)
    assert hit and hit.reset_at == datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)  # 17:00 EDT


def test_a_429_status_is_a_limit_whatever_the_text():
    assert detect_limit({"is_error": True, "api_error_status": 429, "result": "API Error: 429"}, now=NOW)
    assert detect_limit({"is_error": True, "error": {"type": "rate_limit_error", "message": "slow down"}}, now=NOW)


def test_a_rejected_rate_limit_info_gives_its_reset_epoch():
    hit = detect_limit({"is_error": True, "result": "limit reached",
                        "rate_limit_info": {"status": "rejected", "resetsAt": 1792003600}}, now=NOW)
    assert hit and hit.reset_at == datetime.fromtimestamp(1792003600, tz=timezone.utc)


def test_credit_exhaustion_is_a_limit():
    assert detect_limit({"is_error": True, "result": "Credit balance is too low"}, now=NOW)


def test_a_discussion_about_rate_limits_is_never_a_limit():
    answer = ("Should an API apply a rate limit per user or per key? The usage limit reached by heavy tenants is the "
              "usual argument. Hit your limit and you get a 429. " * 3)
    assert detect_limit({"type": "result", "subtype": "success", "is_error": False, "result": answer}, now=NOW) is None
    assert detect_limit_in_text(answer) is None  # too long for the backstop
    assert detect_limit_in_text("We should add a rate limit per key.") is None  # short, but not the platform speaking
    assert not looks_like_platform_limit("The budget and the schedule need a rate limit discussion")


def test_other_errors_are_not_limits():
    assert detect_limit({"is_error": True, "result": "OAuth session expired"}, now=NOW) is None
    assert detect_limit({"is_error": True, "api_error_status": 500, "result": "internal error"}, now=NOW) is None
    assert detect_limit("not a dict") is None and detect_limit({}) is None


def test_the_backstop_reads_short_platform_messages_and_json_results():
    assert detect_limit_in_text("Claude usage limit reached. Your limit will reset at 5pm.")
    assert detect_limit_in_text("You've hit your weekly limit")
    assert detect_limit_in_text(json.dumps({"is_error": True, "result": "5-hour limit reached ∙ resets 3pm"}))
    assert detect_limit_in_text("") is None and detect_limit_in_text(None) is None


def test_reset_parsing_and_the_default_wait():
    assert parse_reset("try again in 20 minutes", now=NOW) == NOW + timedelta(minutes=20)
    assert parse_reset("nothing here", now=NOW) is None
    assert LimitHit("x", None).retry_at(NOW) == NOW + timedelta(minutes=DEFAULT_RETRY_MINUTES)
    assert LimitHit("x", NOW - timedelta(hours=1)).retry_at(NOW) == NOW + timedelta(minutes=DEFAULT_RETRY_MINUTES)  # already past
    later = NOW + timedelta(hours=2)
    assert LimitHit("x", later).retry_at(NOW) == later


def test_the_stream_parser_attaches_the_limit_to_the_result():
    line = json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": "You've hit your weekly limit"})
    ev = parse_stream_line(line)
    assert isinstance(ev, TurnResult) and ev.is_error and ev.limit is not None
    ok = parse_stream_line(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                                       "result": "The weekly limit on requests is a design choice."}))
    assert ok.limit is None and not ok.is_error


async def test_the_cli_client_carries_the_limit_on_its_error(tmp_path):
    p = tmp_path / "claude"
    body = json.dumps({"is_error": True, "result": "Claude AI usage limit reached|1792000000"})
    p.write_text(f"#!/bin/sh\necho '{body}'\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    with pytest.raises(ClaudeCliError) as err:
        await ClaudeCliClient(binary=str(p)).run(model="m", system="s", prompt="p")
    assert err.value.limit is not None and err.value.limit.reset_at is not None
