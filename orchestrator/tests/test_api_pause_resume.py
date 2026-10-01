"""M3.3: pause/resume REST endpoints.

Pure REST-contract tests (no live session actually runs here) -- the runner-level behavior
(run_once() detecting needs_human and returning early, or a resumed runner picking up a
pending grant) is already exercised in test_moderator_runner.py. These tests cover:
  - POST /sessions/{id}/pause: only works on an active session; 404 for unknown; 409 for any
    other status; persists needs_human to the DB.
  - POST /sessions/{id}/resume: only works on a needs_human session; 404 for unknown; 409 for
    any other status; flips status to starting (202); a second tap of /resume 409s.

Real Postgres (live_sessionmaker). SandboxProvider and LLM seams are faked out via
app.dependency_overrides so no sbx CLI or Anthropic credentials are needed.
"""
import asyncio
import uuid
from pathlib import Path

import httpx
import pytest

from fakes import FakeSandboxProvider
from orchestrator.api.deps import get_hand_raise_scorer_factory, get_sandbox_provider, get_summarizer, get_synthesizer
from orchestrator.app import app
from orchestrator.models import Agent, Mode, Session, SessionAgent, Task
from orchestrator.moderator import FakeSummarizer, FakeSynthesizer, SummaryResult, SynthesisResult

FIXTURES = Path(__file__).parent / "fixtures" / "modes"


# ---------------------------------------------------------------- fixtures / helpers

@pytest.fixture(autouse=True)
async def _wire_test_env(monkeypatch, tmp_path, live_sessionmaker):
    """Wire fakes and clean up background tasks, same pattern as test_api_sessions.py."""
    monkeypatch.setenv("BAYTO_MODES_DIR", str(FIXTURES))
    monkeypatch.setenv("BAYTO_FACTORY_ROOT", str(tmp_path / "sessions"))
    app.state.running_sessions.clear()
    app.state.session_runtimes.clear()

    provider = FakeSandboxProvider()
    app.dependency_overrides[get_sandbox_provider] = lambda: provider
    app.dependency_overrides[get_summarizer] = lambda: FakeSummarizer(
        [SummaryResult(summary="s", has_new_argument=True) for _ in range(50)])
    app.dependency_overrides[get_synthesizer] = lambda: FakeSynthesizer(
        SynthesisResult(content_json={"ok": True}, source_turn_ids=[]))
    app.dependency_overrides[get_hand_raise_scorer_factory] = lambda: (lambda: None)

    yield

    app.dependency_overrides.clear()
    for runtime in app.state.session_runtimes.values():
        runtime.mirror_task.cancel()
        runtime.runner_task.cancel()
    for runtime in app.state.session_runtimes.values():
        for task in (runtime.mirror_task, runtime.runner_task):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
    app.state.running_sessions.clear()
    app.state.session_runtimes.clear()


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _make_session(sessionmaker, *, status: str) -> uuid.UUID:
    """A Session row set directly to `status`; no sandbox or runner started."""
    async with sessionmaker() as db:
        task = Task(title="t", output_type="text")
        mode = Mode(name=f"m-{uuid.uuid4()}", phases_json={}, stop_rules_json={})
        agent = Agent(kind="native", name=f"a-{uuid.uuid4()}", role="a")
        db.add_all([task, mode, agent])
        await db.flush()
        session = Session(task_id=task.id, mode_id=mode.id, status=status)
        db.add(session)
        await db.flush()
        db.add(SessionAgent(session_id=session.id, agent_id=agent.id, seat_order=0))
        await db.commit()
        return session.id


async def _db_status(sessionmaker, session_id: uuid.UUID) -> str:
    async with sessionmaker() as db:
        row = await db.get(Session, session_id)
        return row.status


# ---------------------------------------------------------------- pause

async def test_pause_active_session_returns_202(client, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker, status="active")
    resp = await client.post(f"/sessions/{session_id}/pause")
    assert resp.status_code == 202
    assert resp.json()["session"]["status"] == "needs_human"


async def test_pause_persists_needs_human_to_db(client, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker, status="active")
    await client.post(f"/sessions/{session_id}/pause")
    assert await _db_status(live_sessionmaker, session_id) == "needs_human"


async def test_pause_404s_for_unknown_session(client, live_sessionmaker):
    resp = await client.post(f"/sessions/{uuid.uuid4()}/pause")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"]["code"] == "not_found"


async def test_pause_409s_for_already_paused_session(client, live_sessionmaker):
    """Pausing a needs_human session is 409 -- status is not `active`."""
    session_id = await _make_session(live_sessionmaker, status="needs_human")
    resp = await client.post(f"/sessions/{session_id}/pause")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "invalid_transition"


@pytest.mark.parametrize("status", ["created", "starting", "stopping", "cancelling",
                                     "finished", "failed", "orphaned"])
async def test_pause_409s_for_non_active_status(client, live_sessionmaker, status):
    session_id = await _make_session(live_sessionmaker, status=status)
    resp = await client.post(f"/sessions/{session_id}/pause")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "invalid_transition"


# ---------------------------------------------------------------- resume

async def test_resume_paused_session_returns_202(client, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker, status="needs_human")
    resp = await client.post(f"/sessions/{session_id}/resume")
    assert resp.status_code == 202
    assert resp.json()["session"]["status"] == "starting"


async def test_resume_flips_status_to_starting_in_db(client, live_sessionmaker):
    """The DB flip to `starting` happens synchronously (before the fire-and-forget task),
    so it's visible immediately after the response."""
    session_id = await _make_session(live_sessionmaker, status="needs_human")
    await client.post(f"/sessions/{session_id}/resume")
    status = await _db_status(live_sessionmaker, session_id)
    # `starting` OR `active` -- launch_runner races to flip it; either is valid here.
    assert status in ("starting", "active")


async def test_resume_404s_for_unknown_session(client, live_sessionmaker):
    resp = await client.post(f"/sessions/{uuid.uuid4()}/resume")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"]["code"] == "not_found"


async def test_resume_409s_for_active_session(client, live_sessionmaker):
    """Resuming an already-active session would create a second parallel runner."""
    session_id = await _make_session(live_sessionmaker, status="active")
    resp = await client.post(f"/sessions/{session_id}/resume")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "invalid_transition"


@pytest.mark.parametrize("status", ["created", "starting", "stopping", "cancelling",
                                     "finished", "failed", "orphaned"])
async def test_resume_409s_for_non_paused_status(client, live_sessionmaker, status):
    session_id = await _make_session(live_sessionmaker, status=status)
    resp = await client.post(f"/sessions/{session_id}/resume")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "invalid_transition"


async def test_double_resume_tap_409s(client, live_sessionmaker):
    """The first /resume flips DB to `starting`; the second sees `starting`, not
    `needs_human`, and gets 409 -- preventing two parallel runners."""
    session_id = await _make_session(live_sessionmaker, status="needs_human")
    r1 = await client.post(f"/sessions/{session_id}/resume")
    r2 = await client.post(f"/sessions/{session_id}/resume")
    assert r1.status_code == 202
    assert r2.status_code == 409


# ---------------------------------------------------------------- round-trip

async def test_pause_then_resume_round_trip(client, live_sessionmaker):
    """Active -> pause -> needs_human, then resume -> starting (then active via launch_runner)."""
    session_id = await _make_session(live_sessionmaker, status="active")

    pause_resp = await client.post(f"/sessions/{session_id}/pause")
    assert pause_resp.status_code == 202
    assert await _db_status(live_sessionmaker, session_id) == "needs_human"

    resume_resp = await client.post(f"/sessions/{session_id}/resume")
    assert resume_resp.status_code == 202
    status_after = await _db_status(live_sessionmaker, session_id)
    assert status_after in ("starting", "active")
