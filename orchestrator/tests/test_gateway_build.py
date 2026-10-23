"""Route C, narrowed: a Build seat on a gateway model (`BAYTO_ALLOW_GATEWAY_BUILD=1`).

Build turns already run as headless streamed turns (`run_turn`, tool flags from the role's flag file) whenever
`BAYTO_STREAMING_TURNS=1`; only the long-lived seat path lacks a per-seat environment, and a gateway seat never uses it.
Everything here is faked: no real gateway, `sbx` or non-Claude model was involved."""
import asyncio
import logging
import os
import subprocess

import pytest
from acp.modes import ModeConfig
from acp.roster import build_team, load_catalog, tool_cli_args
from sqlalchemy import select

from orchestrator.api.model_catalog import router as models_router
from orchestrator.build_policy import BUILD_ROLES, build_seat_tools
from orchestrator.floor import PipelineFloorPolicy, StopRulesConfig
from orchestrator.models import Message as MessageRow, Session, SessionAgent
from orchestrator.sandbox.local import TURN_SCRIPT, TURN_SCRIPT_OVERRIDE
from orchestrator.sandbox.provider import SeatSpec
from orchestrator.sandbox.provision import write_catalog
from orchestrator.sandbox.turns import TurnResult
from orchestrator.seed import BUILD_TEMPLATES
from fakes import FakeStreamingProvider
from test_api_sessions import _seed, _wire_test_env, client  # noqa: F401  (fixtures)
from test_gateway import SECRET, _fake_home, _run_script, gw  # noqa: F401
from test_moderator_runner import BUILD_ROUTE, CONV_ID, _make_session, _seat, mk_runner

WS = {"repo_path": "/x", "base_branch": "main", "test_command": "uv run pytest", "allowed_commands": ["ruff check"]}
FLAG = "BAYTO_ALLOW_GATEWAY_BUILD"


@pytest.fixture
def allowed(gw, monkeypatch):  # noqa: F811
    monkeypatch.setenv(FLAG, "1")


# ---------------------------------------------------------------- API: off by default, refusals that stay

FOUR = ("build-architect", "build-developer", "build-tester", "build-reviewer")


async def _build_body(live_sessionmaker, model, monkeypatch, **extra):
    monkeypatch.setattr("orchestrator.api.sessions.resolve_mode", lambda name: None)  # no build.yaml in the fixtures
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker, mode_name="build", roles=FOUR)
    from orchestrator.models import Task
    async with live_sessionmaker() as db:
        (await db.get(Task, task_id)).workspace = WS
        await db.commit()
    return {"task_id": str(task_id), "mode_id": str(mode_id),
            # all four Build roles are required by the API; the developer (second) carries the model under test
            "roster": [{"agent_id": str(a), "seat_order": i, **({"model": model} if r == "build-developer" else {})}
                       for i, (a, r) in enumerate(zip(agent_ids, FOUR))], **extra}


async def test_flag_off_keeps_provider_not_supported(client, live_sessionmaker, gw, monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    body = await _build_body(live_sessionmaker, "gateway/qwen3", monkeypatch)
    resp = await client.post("/sessions", json=body)
    assert resp.status_code == 422
    err = resp.json()["detail"]["error"]
    assert err["code"] == "provider_not_supported" and FLAG in err["detail"]
    monkeypatch.setenv(FLAG, "0")  # anything but exactly 1 is off
    assert (await client.post("/sessions", json=body)).status_code == 422


async def test_flag_on_allows_a_gateway_build_seat_and_records_the_model(client, live_sessionmaker, allowed, monkeypatch):
    resp = await client.post("/sessions", json=await _build_body(live_sessionmaker, "gateway/qwen3", monkeypatch))
    assert resp.status_code == 201, resp.text
    assert [sa["model"] for sa in resp.json()["session_agents"]] == [None, "gateway/qwen3", None, None]


async def test_flag_on_still_needs_the_gateway_and_streamed_turns(client, live_sessionmaker, allowed, monkeypatch):
    monkeypatch.delenv("BAYTO_STREAMING_TURNS")
    body = await _build_body(live_sessionmaker, "gateway/qwen3", monkeypatch)
    resp = await client.post("/sessions", json=body)
    assert resp.status_code == 422 and resp.json()["detail"]["error"]["code"] == "provider_not_configured"
    assert "BAYTO_STREAMING_TURNS" in resp.json()["detail"]["error"]["detail"]
    monkeypatch.setenv("BAYTO_STREAMING_TURNS", "1")
    monkeypatch.delenv("BAYTO_GATEWAY_URL")
    resp = await client.post("/sessions", json=body)
    assert resp.status_code == 422 and "BAYTO_GATEWAY_URL" in resp.json()["detail"]["error"]["detail"]


async def test_flag_on_keeps_other_providers_and_the_write_up_claude_only(client, live_sessionmaker, allowed, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("BAYTO_EXTRA_MODELS", "gateway/qwen3:Qwen3:small:0,openai/gpt-x:GPT X:small:0.05")
    body = await _build_body(live_sessionmaker, "openai/gpt-x", monkeypatch)
    resp = await client.post("/sessions", json=body)
    assert resp.status_code == 422 and resp.json()["detail"]["error"]["code"] == "provider_not_supported"
    body["roster"][0]["model"] = "claude-haiku-5-5"
    body["synthesis_model"] = "gateway/qwen3"
    resp = await client.post("/sessions", json=body)
    assert resp.status_code == 422 and resp.json()["detail"]["error"]["code"] == "provider_not_supported"
    assert resp.json()["detail"]["error"]["field"] == "synthesis_model"


def _models(app_client_env=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI(); app.include_router(models_router)
    return {m["id"]: m for m in TestClient(app).get("/models").json()["models"]}


def test_models_reports_build_ok_only_when_allowed(gw, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("BAYTO_EXTRA_MODELS", "gateway/qwen3:Qwen3:small:0,openai/gpt-x:GPT X:small:0.05")
    monkeypatch.delenv(FLAG, raising=False)
    rows = _models()
    assert rows["gateway/qwen3"]["build_ok"] is False and rows["claude-haiku-5-5"]["build_ok"] is True
    monkeypatch.setenv(FLAG, "1")
    rows = _models()
    assert rows["gateway/qwen3"]["build_ok"] is True and rows["openai/gpt-x"]["build_ok"] is False
    assert SECRET not in str(rows)


# ---------------------------------------------------------------- permissions are identical for a gateway seat

def _team(tmp_path, model):
    seats = []
    for tpl in BUILD_TEMPLATES:
        allow, deny = build_seat_tools(tpl["role"], tuple(tpl["tools"]), WS)
        seats.append(SeatSpec(role=tpl["role"], name=tpl["name"], brief=tpl["system_prompt"], tools=allow, deny=deny, model=model))
    roles, roster = tmp_path / "roles", tmp_path / "roster.txt"
    write_catalog(seats, roles, roster)
    entries = [{"role": l.split()[0], **({"model": l.split()[1]} if len(l.split()) > 1 else {})} for l in roster.read_text().splitlines()]
    res = build_team(entries, load_catalog(roles), tmp_path / "out", comms="crew")
    return res, tmp_path / "out"


def test_a_gateway_build_seat_gets_exactly_the_same_allow_and_deny_rules(allowed, tmp_path):
    claude, claude_out = _team(tmp_path / "claude", "claude-sonnet-5-5")
    gateway, gateway_out = _team(tmp_path / "gw", "gateway/qwen3")
    assert set(claude["tools"]) == {t["role"] for t in BUILD_TEMPLATES} and set(claude["tools"]) >= BUILD_ROLES
    for role in claude["tools"]:
        assert gateway["tools"][role] == claude["tools"][role], role
        assert tool_cli_args(gateway["tools"][role]) == tool_cli_args(claude["tools"][role]), role
        assert (gateway_out / "tool-flags" / role).read_text() == (claude_out / "tool-flags" / role).read_text(), role
        assert "--permission-mode\ndontAsk" in (gateway_out / "tool-flags" / role).read_text()
    assert "gateway/qwen3" in (gateway_out / "team.tsv").read_text()  # only the model differs


def test_the_gateway_turn_script_runs_claude_with_the_same_flags_and_only_another_model(tmp_path):
    home = _fake_home(tmp_path, "claude-sonnet-5-5")
    flags = tool_cli_args({"allow": ["Read", "Bash(uv run pytest:*)"], "deny": ["Bash(git push:*)", "WebFetch"]})
    (home / "work" / "factory" / "tool-flags" / "dev").write_text("".join(f"{f}\n" for f in flags))
    plain = _run_script(home, TURN_SCRIPT, ["dev"], "hi\n")
    over = _run_script(home, TURN_SCRIPT_OVERRIDE, ["dev", "qwen3"], f"1\nANTHROPIC_AUTH_TOKEN={SECRET}\nhi\n")
    assert plain.returncode == 0 and over.returncode == 0, over.stderr
    args = lambda done: next(l for l in done.stdout.splitlines() if l.startswith("ARGS=")).replace("claude-sonnet-5-5", "M").replace("qwen3", "M")
    assert args(plain) == args(over)
    assert "--disallowedTools" in args(over) and "dontAsk" in args(over) and "Bash(git push:*)" in args(over)
    assert SECRET not in over.stdout.split("TOKEN=")[0]  # never in the argv line


# ---------------------------------------------------------------- the runner: Build turns on a gateway seat

class _ScriptedStreaming(FakeStreamingProvider):
    """Plays one reply per role, in order, as a streamed turn; records each turn's override."""

    def __init__(self, script):
        super().__init__([])
        self.script = {r: list(v) for r, v in script.items()}

    async def run_turn(self, sandbox, role, prompt, *, env=None, model=None):
        self.prompts.append((role, prompt))
        self.overrides.append((role, env, model))
        yield TurnResult(text=self.script[role].pop(0), tokens_in=1, tokens_out=1, cost=0.0, session_id="s")


class _Pump:
    def __init__(self, mirror, task):
        self._mirror, self._task = mirror, task

    async def cancel(self):  # stop polling, then mirror once more so a last note is in the database
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        await self._mirror.poll_once()


async def _gateway_build(live_sessionmaker, tmp_path, script):
    session_id = await _make_session(live_sessionmaker)
    for i, role in enumerate(["reviewer", "qa-tester", "developer", "architect"]):
        agent_id = await _seat(live_sessionmaker, session_id, role, i)
        if role == "developer":
            async with live_sessionmaker() as db:
                (await db.get(SessionAgent, (session_id, agent_id))).model = "gateway/qwen3"
                await db.commit()
    mode = ModeConfig(name="build", floor_policy="pipeline", roles={"moderator": None, "*": None}, route=BUILD_ROUTE, moderator=None)
    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id, mode=mode,
        policy=PipelineFloorPolicy(BUILD_ROUTE, max_fix_cycles=3), stop_rules=StopRulesConfig(max_fix_cycles=3))
    provider = _ScriptedStreaming(script)
    runner._sandbox_provider = provider

    async def pump():  # the mirror normally polls on a timer; a streamed turn's answer must reach the database
        while True:
            await mirror.poll_once()
            await asyncio.sleep(0.01)

    return session_id, runner, provider, _Pump(mirror, asyncio.create_task(pump()))


SCRIPT = {"architect": ["Plan.\nVERDICT: planned"], "developer": ["Done.\nVERDICT: implemented"],
          "qa-tester": ["```\npytest\n```\nexit 0\nVERDICT: pass"], "reviewer": ["Fine.\nVERDICT: approve"]}


async def test_build_runs_the_gateway_seat_as_a_streamed_turn_and_only_that_seat_gets_the_env(
        live_sessionmaker, tmp_path, allowed, caplog):
    caplog.set_level(logging.DEBUG)
    session_id, runner, provider, pump = await _gateway_build(live_sessionmaker, tmp_path, SCRIPT)
    try:
        for _ in range(12):
            if (await runner.run_once()).stopped:
                break
    finally:
        await pump.cancel()
    by_role = {r: (e, m) for r, e, m in provider.overrides}
    assert [r for r, _, _ in provider.overrides] == ["architect", "developer", "qa-tester", "reviewer"]
    assert by_role["developer"] == ({"ANTHROPIC_BASE_URL": "http://host.docker.internal:4000", "ANTHROPIC_AUTH_TOKEN": SECRET}, "qwen3")
    assert all(by_role[r] == (None, None) for r in ("architect", "qa-tester", "reviewer"))
    assert provider.woken == []  # nothing went through the long-lived seat path
    async with live_sessionmaker() as db:
        assert (await db.get(Session, session_id)).status == "finished"
        stored = " ".join((await db.execute(select(MessageRow.body))).scalars().all())
    # the token is in no prompt, no stored message, no log line and no audit line
    assert SECRET not in stored and SECRET not in caplog.text
    assert not any(SECRET in p for _, p in provider.prompts)


async def test_runtime_flag_off_pauses_the_build_for_a_human_and_runs_nothing_on_another_model(
        live_sessionmaker, tmp_path, allowed, monkeypatch):
    session_id, runner, provider, pump = await _gateway_build(live_sessionmaker, tmp_path, SCRIPT)
    monkeypatch.delenv(FLAG)  # switched off after the session was created
    try:
        for _ in range(4):
            if (await runner.run_once()).stopped:
                break
    finally:
        await pump.cancel()
    assert [r for r, _, _ in provider.overrides] == ["architect"]  # the developer never ran, on any model
    assert provider.woken == []
    async with live_sessionmaker() as db:
        assert (await db.get(Session, session_id)).status == "needs_human"
        notes = (await db.execute(select(MessageRow.body).where(MessageRow.kind == "note"))).scalars().all()
    assert any(FLAG in n for n in notes) and not any(SECRET in n for n in notes)


async def test_a_gateway_build_failure_pauses_with_the_reason_and_never_falls_back(
        live_sessionmaker, tmp_path, allowed):
    session_id, runner, provider, pump = await _gateway_build(live_sessionmaker, tmp_path, SCRIPT)

    orig = provider.run_turn

    async def failing(sandbox, role, prompt, *, env=None, model=None):
        if env:
            provider.overrides.append((role, env, model))
            yield TurnResult(text=f"boom {SECRET}", tokens_in=0, tokens_out=0, cost=0.0, session_id="s", is_error=True)
            return
        async for e in orig(sandbox, role, prompt, env=env, model=model):
            yield e

    provider.run_turn = failing
    try:
        for _ in range(4):
            if (await runner.run_once()).stopped:
                break
    finally:
        await pump.cancel()
    assert provider.woken == [] and [r for r, _, _ in provider.overrides].count("developer") == 1  # no retry elsewhere
    async with live_sessionmaker() as db:
        assert (await db.get(Session, session_id)).status == "needs_human"
        notes = " ".join((await db.execute(select(MessageRow.body).where(MessageRow.kind == "note"))).scalars().all())
    assert "developer" in notes and "gateway model" in notes and SECRET not in notes


async def test_a_build_seat_on_another_provider_is_refused_at_run_time_too(live_sessionmaker, tmp_path, allowed):
    session_id, runner, provider, pump = await _gateway_build(live_sessionmaker, tmp_path, SCRIPT)
    async with live_sessionmaker() as db:
        sa = (await db.execute(select(SessionAgent).where(SessionAgent.model == "gateway/qwen3"))).scalar_one()
        sa.model = "openai/gpt-x"
        await db.commit()
    try:
        for _ in range(4):
            if (await runner.run_once()).stopped:
                break
    finally:
        await pump.cancel()
    assert [r for r, _, _ in provider.overrides] == ["architect"] and provider.woken == []
    async with live_sessionmaker() as db:
        assert (await db.get(Session, session_id)).status == "needs_human"
