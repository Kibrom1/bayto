"""Route A: seats on an Anthropic-compatible gateway (`gateway/<name>`), which still run `claude -p` in the sandbox.

Not run against a real gateway: these tests cover the environment building, the catalogue and API rules, the argv/stdin the
local provider builds, the sandbox-side script run with a fake `claude`, and the runner handing the override to one seat only."""
import asyncio
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from orchestrator import models_catalog
from orchestrator.child_env import child_env
from orchestrator.providers import ProviderError, provider_id_of, provider_needs, provider_ready, split_model_id
from orchestrator.providers.gateway import (PLACEHOLDER_TOKEN, check_override, gateway_env, gateway_host,
                                            gateway_model_name, gateway_url, redact)
from orchestrator.sandbox.local import TURN_SCRIPT, TURN_SCRIPT_OVERRIDE, LocalSbxSandboxProvider
from orchestrator.sandbox.provider import SandboxCommandError, SeatSpec
from orchestrator.sandbox.provision import write_catalog
from test_api_sessions import _wire_test_env, client  # noqa: F401  (fixtures for the API-level tests below)

SECRET = "sk-gw-SENTINEL-1234"


@pytest.fixture
def gw(monkeypatch):
    monkeypatch.setenv("BAYTO_GATEWAY_URL", "http://host.docker.internal:4000/")
    monkeypatch.setenv("BAYTO_GATEWAY_TOKEN", SECRET)
    monkeypatch.setenv("BAYTO_STREAMING_TURNS", "1")
    monkeypatch.setenv("BAYTO_EXTRA_MODELS", "gateway/qwen3:Qwen3 (gateway):small:0")


# ---------------------------------------------------------------- env building

def test_model_ids_and_names():
    assert split_model_id("gateway/qwen3") == ("gateway", "qwen3")
    assert provider_id_of("gateway/qwen3") == "gateway"
    assert split_model_id("gateway/") == ("anthropic", "gateway/")  # nothing after the prefix is not a gateway model
    assert gateway_model_name("gateway/openai/gpt-4o") == "openai/gpt-4o"


def test_gateway_env_holds_the_url_and_token_only(gw):
    assert gateway_env("gateway/qwen3") == {"ANTHROPIC_BASE_URL": "http://host.docker.internal:4000", "ANTHROPIC_AUTH_TOKEN": SECRET}
    assert gateway_host() == "host.docker.internal"


def test_a_gateway_without_a_token_gets_a_placeholder_never_the_claude_login(gw, monkeypatch):
    monkeypatch.delenv("BAYTO_GATEWAY_TOKEN")
    assert gateway_env("gateway/qwen3")["ANTHROPIC_AUTH_TOKEN"] == PLACEHOLDER_TOKEN


def test_gateway_env_refuses_when_unset_or_for_other_models(monkeypatch):
    monkeypatch.delenv("BAYTO_GATEWAY_URL", raising=False)
    with pytest.raises(ProviderError, match="BAYTO_GATEWAY_URL"):
        gateway_env("gateway/qwen3")
    monkeypatch.setenv("BAYTO_GATEWAY_URL", "http://gw:4000")
    for other in ("claude-opus-5-5", "openai/gpt-x", "gateway/"):
        with pytest.raises(ProviderError):
            gateway_env(other)
    monkeypatch.setenv("BAYTO_GATEWAY_URL", "ftp://gw")
    assert gateway_url() is None


def test_override_rules_and_redaction():
    check_override({"ANTHROPIC_BASE_URL": "http://x", "ANTHROPIC_AUTH_TOKEN": "t"})
    for bad in ({"PATH": "/x"}, {"ANTHROPIC_AUTH_TOKEN": "a\nb"}, {"ANTHROPIC_BASE_URL": "a\0"}):
        with pytest.raises(ValueError):
            check_override(bad)
    assert SECRET not in redact(f"boom {SECRET} boom", {"ANTHROPIC_AUTH_TOKEN": SECRET})


# ---------------------------------------------------------------- child_env

def test_child_env_never_passes_the_gateway_settings_but_extra_is_kept(gw):
    env = child_env()
    assert "BAYTO_GATEWAY_TOKEN" not in env and "BAYTO_GATEWAY_URL" not in env
    assert SECRET not in env.values()
    assert child_env({"ANTHROPIC_AUTH_TOKEN": SECRET})["ANTHROPIC_AUTH_TOKEN"] == SECRET  # explicit extra is applied after


# ---------------------------------------------------------------- catalogue, readiness, API-level rules

def test_catalogue_readiness_and_needs(gw, monkeypatch):
    m = models_catalog.get_model("gateway/qwen3")
    assert m.provider == "gateway" and m.tier == "small"
    assert provider_ready("gateway") and provider_needs("gateway") is None
    monkeypatch.delenv("BAYTO_STREAMING_TURNS")
    assert provider_needs("gateway") == "BAYTO_STREAMING_TURNS=1" and not provider_ready("gateway")
    monkeypatch.delenv("BAYTO_GATEWAY_URL")
    assert provider_needs("gateway") == "BAYTO_GATEWAY_URL"


def test_seat_model_rules(gw, monkeypatch):
    check = models_catalog.seat_model_problem
    assert check("gateway/qwen3") is None
    assert check("gateway/qwen3", build=True)[0] == "provider_not_supported"
    assert check("gateway/qwen3", synthesis=True)[0] == "provider_not_supported"
    assert check("gateway/unlisted")[0] == "invalid_model"
    monkeypatch.delenv("BAYTO_GATEWAY_URL")
    code, msg = check("gateway/qwen3")
    assert code == "provider_not_configured" and "BAYTO_GATEWAY_URL" in msg


def test_models_endpoint_shape(gw, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from orchestrator.api.model_catalog import router
    app = FastAPI(); app.include_router(router)
    row = {m["id"]: m for m in TestClient(app).get("/models").json()["models"]}["gateway/qwen3"]
    assert (row["provider"], row["provider_label"], row["ready"], row["needs"]) == ("gateway", "Gateway", True, None)
    monkeypatch.delenv("BAYTO_GATEWAY_URL")
    row = {m["id"]: m for m in TestClient(app).get("/models").json()["models"]}["gateway/qwen3"]
    assert row["ready"] is False and row["needs"] == "BAYTO_GATEWAY_URL"
    assert SECRET not in str(TestClient(app).get("/models").json())


# ---------------------------------------------------------------- provisioning

def test_only_a_gateway_seat_adds_the_gateway_host(gw, tmp_path):
    import yaml
    seats = [SeatSpec(role="a", name="A", brief="x", model="gateway/qwen3"), SeatSpec(role="b", name="B", brief="y", model="claude-sonnet-5-5")]
    write_catalog(seats, tmp_path / "roles", tmp_path / "roster.txt")
    net = lambda r: yaml.safe_load((tmp_path / "roles" / f"{r}.yaml").read_text())["team_needs"]["network"]
    assert net("a") == ["api.anthropic.com", "host.docker.internal"] and net("b") == ["api.anthropic.com"]
    assert (tmp_path / "roster.txt").read_text().splitlines() == ["a gateway/qwen3", "b claude-sonnet-5-5"]
    assert SECRET not in "".join(p.read_text() for p in (tmp_path / "roles").iterdir())


# ---------------------------------------------------------------- local provider: argv, stdin, redaction

class _Runner:
    def __init__(self, lines=(), error=None):
        self.streamed, self.lines, self.error = [], list(lines), error

    def stream(self, argv, *, stdin):
        self.streamed.append((argv, stdin))
        yield from self.lines
        if self.error:
            raise self.error


def _sandbox():
    from orchestrator.sandbox.provider import SandboxInfo
    return SandboxInfo(id=uuid.uuid4(), task_id=uuid.uuid4(), provider="local-sbx", name="sbx-1", status="running",
                       image=None, created_at=None, closed_at=None)


RESULT = '{"type":"result","subtype":"success","result":"ok","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0,"session_id":"s"}'


async def test_override_turn_puts_the_token_on_stdin_never_in_argv(live_sessionmaker):
    runner = _Runner([RESULT])
    provider = LocalSbxSandboxProvider(live_sessionmaker, runner=runner)
    env = {"ANTHROPIC_BASE_URL": "http://gw:4000", "ANTHROPIC_AUTH_TOKEN": SECRET}
    events = [e async for e in provider.run_turn(_sandbox(), "dev", "the prompt", env=env, model="qwen3")]
    assert events
    argv, stdin = runner.streamed[0]
    assert argv[-2:] == ["dev", "qwen3"] and argv[5] == TURN_SCRIPT_OVERRIDE
    assert SECRET not in " ".join(argv)
    assert stdin == f"2\nANTHROPIC_BASE_URL=http://gw:4000\nANTHROPIC_AUTH_TOKEN={SECRET}\nthe prompt"


async def test_a_turn_without_an_override_is_unchanged(live_sessionmaker):
    runner = _Runner([RESULT])
    provider = LocalSbxSandboxProvider(live_sessionmaker, runner=runner)
    [e async for e in provider.run_turn(_sandbox(), "dev", "the prompt")]
    argv, stdin = runner.streamed[0]
    assert argv[5] == TURN_SCRIPT and argv[-1] == "dev" and stdin == "the prompt"


async def test_a_failed_override_turn_does_not_leak_the_token(live_sessionmaker):
    boom = SandboxCommandError(["exec"], 1, f"claude: auth failed for {SECRET}")
    provider = LocalSbxSandboxProvider(live_sessionmaker, runner=_Runner(error=boom))
    with pytest.raises(SandboxCommandError) as err:
        [e async for e in provider.run_turn(_sandbox(), "dev", "p", env={"ANTHROPIC_AUTH_TOKEN": SECRET}, model="m")]
    assert SECRET not in str(err.value) and "[redacted]" in err.value.stderr


async def test_an_override_with_a_non_anthropic_variable_is_refused(live_sessionmaker):
    provider = LocalSbxSandboxProvider(live_sessionmaker, runner=_Runner([RESULT]))
    with pytest.raises(ValueError):
        [e async for e in provider.run_turn(_sandbox(), "dev", "p", env={"LD_PRELOAD": "x"}, model="m")]


# ---------------------------------------------------------------- the sandbox-side script, run for real with a fake `claude`

def _fake_home(tmp_path: Path, model_in_team: str) -> Path:
    home = tmp_path / "home"
    (home / "work" / "app").mkdir(parents=True)
    (home / "work" / "bin").mkdir()
    (home / "work" / "roles").mkdir()
    (home / "work" / "factory" / "tool-flags").mkdir(parents=True)
    (home / "work" / "team.tsv").write_text(f"dev\tclaude\tanthropic\t{model_in_team}\n")
    (home / "work" / "roles" / "dev.md").write_text("brief")
    (home / "work" / "factory" / "tool-flags" / "dev").write_text("--allowedTools\nRead\n")
    claude = home / "work" / "bin" / "claude"
    claude.write_text('#!/bin/bash\necho "ARGS=$*"\necho "BASE=${ANTHROPIC_BASE_URL-unset}"\necho "TOKEN=${ANTHROPIC_AUTH_TOKEN-unset}"\n'
                      'echo "APIKEY=${ANTHROPIC_API_KEY-unset}"\necho "STDIN=$(cat)"\n')
    claude.chmod(0o755)
    return home


def _run_script(home: Path, script: str, args: list[str], stdin: str):
    env = {"HOME": str(home), "PATH": os.environ["PATH"], "ANTHROPIC_API_KEY": "sandbox-global-key"}
    return subprocess.run(["bash", "-c", script, "_", *args], input=stdin, capture_output=True, text=True, env=env, timeout=20)


def test_script_applies_env_and_model_only_for_the_override_and_keeps_the_prompt(tmp_path):
    home = _fake_home(tmp_path, "gateway/qwen3")
    done = _run_script(home, TURN_SCRIPT_OVERRIDE, ["dev", "qwen3"],
                       f"2\nANTHROPIC_BASE_URL=http://gw:4000\nANTHROPIC_AUTH_TOKEN={SECRET}\nline one\nline two\n")
    assert done.returncode == 0, done.stderr
    out = dict(l.split("=", 1) for l in done.stdout.splitlines() if "=" in l)
    assert "--model qwen3 " in out["ARGS"]  # the bare name, not gateway/qwen3
    assert out["BASE"] == "http://gw:4000" and out["TOKEN"] == SECRET
    assert out["APIKEY"] == "unset"  # the sandbox's own key is never sent to the gateway
    assert out["STDIN"] == "line one"  # first stdin line of the prompt; the next is on the following output line
    assert "line two" in done.stdout


def test_script_without_override_sets_nothing_and_reads_the_model_from_team_tsv(tmp_path):
    home = _fake_home(tmp_path, "claude-haiku-5-5")
    done = _run_script(home, TURN_SCRIPT, ["dev"], "hello\n")
    assert done.returncode == 0, done.stderr
    assert "--model claude-haiku-5-5 " in done.stdout and "BASE=unset" in done.stdout and "TOKEN=unset" in done.stdout


def test_script_refuses_a_variable_that_is_not_anthropic(tmp_path):
    home = _fake_home(tmp_path, "x")
    done = _run_script(home, TURN_SCRIPT_OVERRIDE, ["dev", "m"], "1\nLD_PRELOAD=/evil.so\nprompt\n")
    assert done.returncode == 2 and "LD_PRELOAD" not in done.stdout


# ---------------------------------------------------------------- the runner: the override reaches only the gateway seat

async def test_gateway_env_reaches_only_the_gateway_seat(client, live_sessionmaker, gw):
    from fakes import FakeStreamingProvider
    from orchestrator.sandbox.turns import TurnResult
    from test_api_sessions import _seed, _wait_for_status, _wire_fakes

    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    seats = [{"agent_id": str(agent_ids[0]), "seat_order": 0, "model": "gateway/qwen3"},
             {"agent_id": str(agent_ids[1]), "seat_order": 1, "model": "claude-haiku-5-5"}]
    resp = await client.post("/sessions", json={"task_id": str(task_id), "mode_id": str(mode_id), "roster": seats})
    assert resp.status_code == 201, resp.text
    session_id = uuid.UUID(resp.json()["session"]["id"])
    provider = FakeStreamingProvider([TurnResult(text="hi", tokens_in=1, tokens_out=1, cost=0, session_id="s")])
    _wire_fakes(provider)
    await client.post(f"/sessions/{session_id}/start")
    await _wait_for_status(live_sessionmaker, session_id, "active")
    for _ in range(200):
        if {r for r, _, _ in provider.overrides} >= {"a", "b"}:
            break
        await asyncio.sleep(0.05)
    await client.post(f"/sessions/{session_id}/stop", json={"synthesize": False})
    by_role = {}
    for role, env, model in provider.overrides:
        by_role.setdefault(role, []).append((env, model))
    assert all(e == {"ANTHROPIC_BASE_URL": "http://host.docker.internal:4000", "ANTHROPIC_AUTH_TOKEN": SECRET} and m == "qwen3"
               for e, m in by_role["a"])
    assert all(e is None and m is None for e, m in by_role["b"])  # the other seat gets no override at all
    assert provider.woken == []  # nobody was woken through the long-lived seat path (no silent fallback)
    assert not any(SECRET in p for _, p in provider.prompts)


async def test_a_gateway_seat_fails_loudly_instead_of_falling_back(client, live_sessionmaker, gw, monkeypatch):
    """The sandbox cannot stream (so no per-seat env): the seat must not be woken through the old path or answered by Claude."""
    from test_api_sessions import _seed, _wire_fakes
    from fakes import FakeSandboxProvider

    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={"task_id": str(task_id), "mode_id": str(mode_id),
                                                "roster": [{"agent_id": str(agent_ids[0]), "seat_order": 0, "model": "gateway/qwen3"}]})
    session_id = uuid.UUID(resp.json()["session"]["id"])
    woken = []

    async def on_wake(role):
        woken.append(role)

    _wire_fakes(FakeSandboxProvider(on_wake=on_wake))
    await client.post(f"/sessions/{session_id}/start")
    from orchestrator.models import Session
    status = None
    for _ in range(100):
        async with live_sessionmaker() as db:
            status = (await db.get(Session, session_id)).status
        if status in ("needs_human", "failed"):
            break
        await asyncio.sleep(0.05)
    assert status in ("needs_human", "failed") and woken == []


async def test_build_and_synthesis_refuse_a_gateway_model(client, live_sessionmaker, gw):
    from test_api_sessions import _seed
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    body = {"task_id": str(task_id), "mode_id": str(mode_id),
            "roster": [{"agent_id": str(agent_ids[0]), "seat_order": 0}], "synthesis_model": "gateway/qwen3"}
    resp = await client.post("/sessions", json=body)
    assert resp.status_code == 422 and resp.json()["detail"]["error"]["code"] == "provider_not_supported"
