"""Fail fast when the moderator's `claude` login is missing or expired (checked at Start and Resume)."""
import uuid

import httpx
import pytest

from orchestrator.api import deps
from orchestrator.app import app
from orchestrator.llm import claude_cli
from orchestrator.llm.claude_cli import ClaudeCliError, check_login, reset_login_cache
from orchestrator.models import Agent, Mode, Session, SessionAgent, Task


class _Cli:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self._error = error

    async def run(self, *, model, system, prompt):
        self.calls += 1
        if self._error:
            raise self._error
        return {"result": "ok", "in": 1, "out": 1}


@pytest.fixture(autouse=True)
def _fresh_cache():
    reset_login_cache()
    yield
    reset_login_cache()


async def test_check_login_makes_one_call_then_is_cached():
    cli = _Cli()
    await check_login(client=cli)
    await check_login(client=cli)
    assert cli.calls == 1


async def test_check_login_raises_the_cli_reason_and_does_not_cache_a_failure():
    bad = _Cli(ClaudeCliError("claude -p exited 1: Failed to authenticate: OAuth session expired"))
    with pytest.raises(ClaudeCliError, match="OAuth session expired"):
        await check_login(client=bad)
    good = _Cli()
    await check_login(client=good)
    assert good.calls == 1


async def test_default_check_does_nothing_for_the_api_backend(monkeypatch):
    monkeypatch.setenv("BAYTO_LLM_BACKEND", "api")

    async def boom(*a, **k):
        raise AssertionError("must not probe the CLI for the API backend")
    monkeypatch.setattr(claude_cli, "check_login", boom)
    await deps.default_login_check()


async def test_default_check_turns_a_cli_failure_into_a_clear_503(monkeypatch):
    monkeypatch.setenv("BAYTO_LLM_BACKEND", "cli")

    async def expired(*a, **k):
        raise ClaudeCliError("claude -p exited 1: OAuth session expired")
    monkeypatch.setattr(claude_cli, "check_login", expired)
    with pytest.raises(Exception) as ei:
        await deps.default_login_check()
    exc = ei.value
    assert exc.status_code == 503
    assert exc.detail["error"]["code"] == "moderator_login"
    assert "/login" in exc.detail["error"]["detail"] and "OAuth session expired" in exc.detail["error"]["detail"]


async def _session(sessionmaker, status: str) -> uuid.UUID:
    async with sessionmaker() as db:
        task = Task(title="t", output_type="text")
        mode = Mode(name=f"m-{uuid.uuid4()}", phases_json={}, stop_rules_json={})
        agent = Agent(kind="native", name=f"a-{uuid.uuid4()}", role="a")
        db.add_all([task, mode, agent])
        await db.flush()
        s = Session(task_id=task.id, mode_id=mode.id, status=status)
        db.add(s)
        await db.flush()
        db.add(SessionAgent(session_id=s.id, agent_id=agent.id, seat_order=0))
        await db.commit()
        return s.id


@pytest.mark.parametrize("path,status", [("start", "created"), ("resume", "needs_human")])
async def test_start_and_resume_stop_before_touching_the_session_when_login_is_bad(live_sessionmaker, monkeypatch, path, status):
    for v in ("BAYTO_SUMMARY_MODEL", "BAYTO_SYNTHESIS_MODEL", "BAYTO_HAND_RAISE_MODEL"):
        monkeypatch.setenv(v, "m")
    async def refuse():
        raise deps.HTTPException(503, detail={"error": {"code": "moderator_login", "detail": "run /login"}})
    app.state.login_check = refuse
    sid = await _session(live_sessionmaker, status)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(f"/sessions/{sid}/{path}")
    assert r.status_code == 503 and r.json()["detail"]["error"]["code"] == "moderator_login"
    async with live_sessionmaker() as db:
        assert (await db.get(Session, sid)).status == status
