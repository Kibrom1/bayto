"""Per-seat failover from Claude to a local model when Claude is out of usage, with fakes only: a scripted streaming
provider stands in for `claude`, an httpx MockTransport stands in for Ollama. No real Claude limit and no real Ollama
server was involved (see docs/per-agent-models.md)."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from orchestrator.limits import detect_limit
from orchestrator.floor import RoundRobinFloorPolicy
from acp.transport import FileTransport
from orchestrator.mirror import MessageMirror
from orchestrator.models import Message as MessageRow
from orchestrator.models import Session, Turn
from orchestrator.providers.http_providers import OpenAICompatProvider
from orchestrator.sandbox.turns import TextDelta, TurnResult

from audit_helpers import audit_events
from fakes import FakeStreamingProvider
from test_moderator_runner import (_make_session, _respond, _seat, _set_task, _build_session, mk_mode_config, mk_runner)

_PAYLOAD = {"type": "result", "subtype": "success", "is_error": True, "result": "Claude AI usage limit reached|1792000000"}
LIMIT = TurnResult(text=_PAYLOAD["result"], tokens_in=0, tokens_out=0, cost=None, session_id="s", is_error=True,
                   limit=detect_limit(_PAYLOAD))
GOOD = TurnResult(text="claude says hi", tokens_in=10, tokens_out=5, cost=0.01, session_id="s")


class ScriptedStreaming(FakeStreamingProvider):
    """One list of events per turn, consumed in order (the last one repeats)."""

    def __init__(self, scripts, **kw):
        super().__init__([], **kw)
        self.scripts = list(scripts)

    async def run_turn(self, sandbox, role, prompt, *, env=None, model=None):
        self.prompts.append((role, prompt))
        self.overrides.append((role, env, model))
        for e in (self.scripts.pop(0) if len(self.scripts) > 1 else self.scripts[0]):
            yield e


@pytest.fixture
def ollama(monkeypatch):
    """Ollama is set up and answers through a MockTransport; every request body is kept."""
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    monkeypatch.setenv("BAYTO_EXTRA_MODELS", "ollama/llama3:Llama 3:small:0")
    seen: list[dict] = []
    state = {"fail": False}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        if state["fail"]:
            return httpx.Response(500, json={"error": {"message": "model not loaded"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "local says hi"}}],
                                         "usage": {"prompt_tokens": 50, "completion_tokens": 7}})

    monkeypatch.setattr("orchestrator.moderator.runner.provider_for",
                        lambda pid: OpenAICompatProvider("ollama", base_url="http://x/v1", api_key=None,
                                                         transport=httpx.MockTransport(handler)))
    seen_holder = type("O", (), {"requests": seen, "state": state})
    return seen_holder


async def _setup(tmp_path, live_sessionmaker, scripts, *, fallback=True):
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    await _set_task(live_sessionmaker, session_id, title="Weekly or biweekly releases?",
                    brief="Decide the release cadence.",
                    **({"local_fallback": {"enabled": True, "model": "ollama/llama3"}} if fallback else {}))
    runner, transport, mirror, _ = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mk_mode_config(), policy=RoundRobinFloorPolicy())
    provider = ScriptedStreaming(scripts)
    runner._sandbox_provider = provider
    return session_id, runner, mirror, provider


async def _turn(runner, mirror):
    async def mirror_later():
        for _ in range(60):
            await asyncio.sleep(0.02)
            await mirror.poll_once()
    task = asyncio.create_task(mirror_later())
    try:
        return await runner.run_once()
    finally:
        task.cancel()


async def _messages(sm, session_id, mirror):
    await mirror.poll_once()  # notices go to the message log; the database sees them once the mirror has polled
    async with sm() as db:
        return (await db.execute(select(MessageRow).where(MessageRow.session_id == session_id).order_by(MessageRow.seq))).scalars().all()


async def _state(sm, session_id):
    async with sm() as db:
        return (await db.get(Session, session_id)).fallback_state


async def test_a_limit_moves_one_seat_to_the_local_model_with_the_full_prompt(tmp_path, live_sessionmaker, ollama, span_exporter):
    session_id, runner, mirror, provider = await _setup(tmp_path, live_sessionmaker, [[LIMIT]])
    await _turn(runner, mirror)

    # the local model got the same prompt the Claude path was given: the task, brief and the assignment, not a bare line
    claude_prompt = provider.prompts[0][1]
    sent = ollama.requests[0]
    user = next(m["content"] for m in sent["messages"] if m["role"] == "user")
    assert sent["model"] == "llama3" and user == claude_prompt
    assert "Task: Weekly or biweekly releases?" in user and "Brief: Decide the release cadence." in user
    assert provider.woken == []  # no second try on the Claude seat path

    msgs = await _messages(live_sessionmaker, session_id, mirror)
    answer = next(m for m in msgs if m.kind == "answer")
    assert answer.body == "local says hi" and answer.meta["local_fallback"]["model"] == "ollama/llama3"
    assert answer.meta["provider"] == "ollama"
    notice = next(m for m in msgs if m.kind == "note")
    assert "usage limit was reached" in notice.body and "ollama/llama3" in notice.body and "marked" in notice.body
    state = await _state(live_sessionmaker, session_id)
    assert state["a"]["model"] == "ollama/llama3"
    assert datetime.fromisoformat(state["a"]["retry_at"]) > datetime.now(timezone.utc)  # the reset time (default wait if past)
    async with live_sessionmaker() as db:
        assert (await db.execute(select(Turn).where(Turn.session_id == session_id))).scalars().one().content == "local says hi"
    actions = {e.action for e in audit_events(span_exporter, session_id=session_id)}
    assert {"turn.claude_limit", "turn.local_fallback"} <= actions


async def test_while_waiting_for_the_reset_claude_is_not_called_and_no_second_notice_is_posted(tmp_path, live_sessionmaker, ollama):
    session_id, runner, mirror, provider = await _setup(tmp_path, live_sessionmaker, [[LIMIT]])
    await _turn(runner, mirror)
    await _turn(runner, mirror)
    assert len(provider.prompts) == 1  # the second turn went straight to the local model
    assert len(ollama.requests) == 2
    msgs = await _messages(live_sessionmaker, session_id, mirror)
    assert sum(m.kind == "note" for m in msgs) == 1
    assert sum(1 for m in msgs if m.kind == "answer" and m.meta.get("local_fallback")) == 2


async def _expire(sm, session_id):
    async with sm() as db:
        row = await db.get(Session, session_id)
        row.fallback_state = {k: {**v, "retry_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}
                              for k, v in row.fallback_state.items()}
        await db.commit()


async def test_after_the_reset_claude_is_probed_and_the_seat_returns_to_it(tmp_path, live_sessionmaker, ollama):
    session_id, runner, mirror, provider = await _setup(tmp_path, live_sessionmaker, [[LIMIT], [TextDelta("c"), GOOD]])
    await _turn(runner, mirror)
    await _expire(live_sessionmaker, session_id)
    await _turn(runner, mirror)
    assert len(provider.prompts) == 2 and len(ollama.requests) == 1
    assert await _state(live_sessionmaker, session_id) is None
    msgs = await _messages(live_sessionmaker, session_id, mirror)
    last = [m for m in msgs if m.kind == "answer"][-1]
    assert last.body == "claude says hi" and "local_fallback" not in (last.meta or {})
    assert any("back on Claude" in m.body for m in msgs if m.kind == "note")


async def test_a_probe_that_still_hits_the_limit_stays_local_and_waits_again(tmp_path, live_sessionmaker, ollama):
    session_id, runner, mirror, provider = await _setup(tmp_path, live_sessionmaker, [[LIMIT]])
    await _turn(runner, mirror)
    await _expire(live_sessionmaker, session_id)
    await _turn(runner, mirror)
    assert len(provider.prompts) == 2 and len(ollama.requests) == 2
    state = await _state(live_sessionmaker, session_id)
    assert datetime.fromisoformat(state["a"]["retry_at"]) > datetime.now(timezone.utc)
    msgs = await _messages(live_sessionmaker, session_id, mirror)
    assert sum(m.kind == "note" for m in msgs) == 1  # no repeat notice


async def test_with_the_option_off_a_limit_pauses_for_a_human_and_does_not_loop(tmp_path, live_sessionmaker, ollama):
    session_id, runner, mirror, provider = await _setup(tmp_path, live_sessionmaker, [[LIMIT]], fallback=False)
    await _turn(runner, mirror)
    assert ollama.requests == [] and provider.woken == [] and len(provider.prompts) == 1
    async with live_sessionmaker() as db:
        assert (await db.get(Session, session_id)).status == "needs_human"
    note = next(m for m in await _messages(live_sessionmaker, session_id, mirror) if m.kind == "note")
    assert "usage limit was reached" in note.body and "local model fallback is off" in note.body and "Paused" in note.body


async def test_a_build_seat_never_falls_back(tmp_path, live_sessionmaker, ollama):
    session_id, runner, sandbox = await _build_session(live_sessionmaker, tmp_path, {})
    mirror = MessageMirror(FileTransport(tmp_path), live_sessionmaker, runner._pubsub, session_id=session_id)
    await _set_task(live_sessionmaker, session_id, local_fallback={"enabled": True, "model": "ollama/llama3"})
    provider = ScriptedStreaming([[LIMIT]])
    runner._sandbox_provider = provider
    await runner.run_once()
    assert ollama.requests == [] and len(provider.prompts) == 1
    async with live_sessionmaker() as db:
        assert (await db.get(Session, session_id)).status == "needs_human"
    note = next(m for m in await _messages(live_sessionmaker, session_id, mirror) if m.kind == "note")
    assert "fall back only to a gateway model" in note.body and "BAYTO_BUILD_FALLBACK_MODEL" in note.body


@pytest.fixture
def build_fallback(monkeypatch):
    monkeypatch.setenv("BAYTO_GATEWAY_URL", "http://host.docker.internal:11434")
    monkeypatch.setenv("BAYTO_ALLOW_GATEWAY_BUILD", "1")
    monkeypatch.setenv("BAYTO_STREAMING_TURNS", "1")
    monkeypatch.setenv("BAYTO_BUILD_FALLBACK_MODEL", "qwen2.5-coder:14b")


def test_the_build_fallback_needs_all_three_settings(monkeypatch):
    from orchestrator.providers.gateway import build_fallback_model
    for k in ("BAYTO_GATEWAY_URL", "BAYTO_ALLOW_GATEWAY_BUILD", "BAYTO_BUILD_FALLBACK_MODEL"):
        monkeypatch.delenv(k, raising=False)
    assert build_fallback_model() is None
    monkeypatch.setenv("BAYTO_BUILD_FALLBACK_MODEL", "qwen2.5-coder:14b")
    assert build_fallback_model() is None  # no gateway, Build not allowed
    monkeypatch.setenv("BAYTO_GATEWAY_URL", "http://host.docker.internal:11434")
    assert build_fallback_model() is None  # Build seats may not use a gateway yet
    monkeypatch.setenv("BAYTO_ALLOW_GATEWAY_BUILD", "1")
    assert build_fallback_model() == "qwen2.5-coder:14b"
    monkeypatch.setenv("BAYTO_BUILD_FALLBACK_MODEL", "bad name\nx")
    assert build_fallback_model() is None


async def test_a_build_seat_on_a_limit_retries_the_turn_on_the_gateway_model_with_its_tools(tmp_path, live_sessionmaker, build_fallback):
    session_id, runner, sandbox = await _build_session(live_sessionmaker, tmp_path, {})
    mirror = MessageMirror(FileTransport(tmp_path), live_sessionmaker, runner._pubsub, session_id=session_id)
    provider = ScriptedStreaming([[LIMIT], [GOOD]])
    runner._sandbox_provider = provider
    await _turn(runner, mirror)
    assert len(provider.prompts) == 2  # Claude once, then the local model once: no loop
    first, second = provider.overrides
    assert first[1] is None and first[2] is None  # the first try is plain Claude
    assert second[2] == "qwen2.5-coder:14b" and second[1]["ANTHROPIC_BASE_URL"] == "http://host.docker.internal:11434"
    assert provider.prompts[0][0] == provider.prompts[1][0]  # the same seat, so the same tool-permission flags apply
    async with live_sessionmaker() as db:
        assert (await db.get(Session, session_id)).status != "needs_human"
    state = await _state(live_sessionmaker, session_id)
    assert next(iter(state.values()))["model"] == "qwen2.5-coder:14b"
    notes = [m for m in await _messages(live_sessionmaker, session_id, mirror) if m.kind == "note"]
    assert any("usage limit was reached" in m.body and "qwen2.5-coder:14b" in m.body for m in notes)


async def test_if_the_build_gateway_model_fails_the_session_pauses_with_both_reasons(tmp_path, live_sessionmaker, build_fallback):
    session_id, runner, sandbox = await _build_session(live_sessionmaker, tmp_path, {})
    mirror = MessageMirror(FileTransport(tmp_path), live_sessionmaker, runner._pubsub, session_id=session_id)
    provider = ScriptedStreaming([[LIMIT], []])  # the second turn ends with no result at all
    runner._sandbox_provider = provider
    await _turn(runner, mirror)
    assert len(provider.prompts) == 2
    async with live_sessionmaker() as db:
        assert (await db.get(Session, session_id)).status == "needs_human"
    note = [m for m in await _messages(live_sessionmaker, session_id, mirror) if m.kind == "note"][-1]
    assert "usage limit was reached" in note.body and "did not answer" in note.body


async def test_if_the_local_model_fails_the_session_pauses_with_both_reasons(tmp_path, live_sessionmaker, ollama):
    ollama.state["fail"] = True
    session_id, runner, mirror, provider = await _setup(tmp_path, live_sessionmaker, [[LIMIT]])
    await _turn(runner, mirror)
    async with live_sessionmaker() as db:
        assert (await db.get(Session, session_id)).status == "needs_human"
    note = [m for m in await _messages(live_sessionmaker, session_id, mirror) if m.kind == "note"][-1]
    assert "usage limit was reached" in note.body and "did not answer" in note.body
    assert len(ollama.requests) >= 1 and provider.woken == []  # one local attempt per turn, no looping on Claude


async def test_a_limit_message_on_the_seat_path_is_caught_by_the_backstop(tmp_path, live_sessionmaker, ollama):
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    await _set_task(live_sessionmaker, session_id, local_fallback={"enabled": True, "model": "ollama/llama3"})

    async def on_wake(role):
        await _respond(transport, mirror, role, "Claude usage limit reached. Your limit will reset at 5pm.")

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mk_mode_config(), policy=RoundRobinFloorPolicy(), on_wake=on_wake)
    await _turn(runner, mirror)
    assert len(ollama.requests) == 1 and sandbox.woken == ["a"]  # woken once, not re-woken in a loop
    assert (await _state(live_sessionmaker, session_id))["a"]["model"] == "ollama/llama3"


async def test_a_short_answer_about_rate_limits_is_a_normal_turn(tmp_path, live_sessionmaker, ollama):
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    await _set_task(live_sessionmaker, session_id, local_fallback={"enabled": True, "model": "ollama/llama3"})

    async def on_wake(role):
        await _respond(transport, mirror, role, "We should add a rate limit per key.")

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mk_mode_config(), policy=RoundRobinFloorPolicy(), on_wake=on_wake)
    await runner.run_once()
    assert ollama.requests == [] and await _state(live_sessionmaker, session_id) is None
    async with live_sessionmaker() as db:
        assert (await db.execute(select(Turn).where(Turn.session_id == session_id))).scalars().one().content.startswith("We should")


async def test_the_local_turn_publishes_a_message_meta_event(tmp_path, live_sessionmaker, ollama):
    session_id, runner, mirror, provider = await _setup(tmp_path, live_sessionmaker, [[LIMIT]])
    q = runner._pubsub.subscribe(bounded=False)
    await _turn(runner, mirror)
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    meta = next(e for e in events if e["type"] == "message_meta")
    assert meta["data"]["meta"]["local_fallback"]["model"] == "ollama/llama3" and meta["data"]["from"] == "a"
