"""M3.2: participant lifecycle REST endpoints -- mute/unmute/remove/add. Pure REST-contract
tests against directly-constructed fixture rows; no live session actually runs here (the
runner-level behavior these persist for -- a muted/removed participant actually gets
skipped, a newly-added one actually gets granted -- is covered end to end in
test_moderator_runner.py, against real ModeratorRunner.run_once() calls). Real Postgres, no
sbx or Anthropic needed."""
import uuid

import httpx

from orchestrator.app import app
from orchestrator.models import Agent, Mode, Session, SessionAgent, Task


async def _make_live_session(sessionmaker, *, status="active") -> tuple[uuid.UUID, uuid.UUID]:
    """A Session row set directly to `status` and one seated participant -- no sandbox or
    runner actually started, since these endpoints only ever read/write Session/
    SessionAgent rows."""
    async with sessionmaker() as db:
        task = Task(title="t", output_type="text")
        mode = Mode(name=f"m-{uuid.uuid4()}", phases_json={}, stop_rules_json={})
        db.add_all([task, mode])
        await db.flush()
        session = Session(task_id=task.id, mode_id=mode.id, status=status)
        agent = Agent(kind="native", name="a", role="a")
        db.add_all([session, agent])
        await db.flush()
        db.add(SessionAgent(session_id=session.id, agent_id=agent.id, seat_order=0))
        await db.commit()
        return session.id, agent.id


async def _make_agent(sessionmaker, *, name="b") -> uuid.UUID:
    async with sessionmaker() as db:
        agent = Agent(kind="native", name=name, role=name)
        db.add(agent)
        await db.commit()
        await db.refresh(agent)
        return agent.id


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------- mute / unmute

async def test_mute_sets_muted_true(live_sessionmaker):
    session_id, agent_id = await _make_live_session(live_sessionmaker)
    async with _client() as client:
        resp = await client.post(f"/sessions/{session_id}/participants/{agent_id}/mute")
    assert resp.status_code == 200
    assert resp.json()["session_agent"]["muted"] is True


async def test_unmute_sets_muted_false(live_sessionmaker):
    session_id, agent_id = await _make_live_session(live_sessionmaker)
    async with _client() as client:
        await client.post(f"/sessions/{session_id}/participants/{agent_id}/mute")
        resp = await client.post(f"/sessions/{session_id}/participants/{agent_id}/unmute")
    assert resp.status_code == 200
    assert resp.json()["session_agent"]["muted"] is False


async def test_mute_404s_for_an_unknown_session(live_sessionmaker):
    _, agent_id = await _make_live_session(live_sessionmaker)
    async with _client() as client:
        resp = await client.post(f"/sessions/{uuid.uuid4()}/participants/{agent_id}/mute")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"]["field"] == "id"


async def test_mute_404s_for_an_agent_that_was_never_a_participant(live_sessionmaker):
    session_id, _ = await _make_live_session(live_sessionmaker)
    async with _client() as client:
        resp = await client.post(f"/sessions/{session_id}/participants/{uuid.uuid4()}/mute")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"]["field"] == "agent_id"


async def test_mute_409s_when_the_session_is_not_live(live_sessionmaker):
    session_id, agent_id = await _make_live_session(live_sessionmaker, status="finished")
    async with _client() as client:
        resp = await client.post(f"/sessions/{session_id}/participants/{agent_id}/mute")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "session_not_active"


async def test_unmute_409s_when_the_session_is_not_live(live_sessionmaker):
    session_id, agent_id = await _make_live_session(live_sessionmaker, status="finished")
    async with _client() as client:
        resp = await client.post(f"/sessions/{session_id}/participants/{agent_id}/unmute")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "session_not_active"


async def test_mute_is_allowed_while_the_session_is_paused_for_a_human(live_sessionmaker):
    session_id, agent_id = await _make_live_session(live_sessionmaker, status="needs_human")
    async with _client() as client:
        resp = await client.post(f"/sessions/{session_id}/participants/{agent_id}/mute")
    assert resp.status_code == 200


# ---------------------------------------------------------------- remove

async def test_remove_sets_removed_at(live_sessionmaker):
    session_id, agent_id = await _make_live_session(live_sessionmaker)
    async with _client() as client:
        resp = await client.post(f"/sessions/{session_id}/participants/{agent_id}/remove")
    assert resp.status_code == 200
    assert resp.json()["session_agent"]["removed_at"] is not None
    assert resp.json()["session_agent"]["muted"] is False  # remove leaves `muted` untouched


async def test_remove_is_idempotent_and_keeps_the_original_timestamp(live_sessionmaker):
    session_id, agent_id = await _make_live_session(live_sessionmaker)
    async with _client() as client:
        first = await client.post(f"/sessions/{session_id}/participants/{agent_id}/remove")
        second = await client.post(f"/sessions/{session_id}/participants/{agent_id}/remove")
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["session_agent"]["removed_at"] == second.json()["session_agent"]["removed_at"]


async def test_remove_409s_when_the_session_is_not_live(live_sessionmaker):
    session_id, agent_id = await _make_live_session(live_sessionmaker, status="finished")
    async with _client() as client:
        resp = await client.post(f"/sessions/{session_id}/participants/{agent_id}/remove")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "session_not_active"


async def test_remove_404s_for_an_agent_that_was_never_a_participant(live_sessionmaker):
    session_id, _ = await _make_live_session(live_sessionmaker)
    async with _client() as client:
        resp = await client.post(f"/sessions/{session_id}/participants/{uuid.uuid4()}/remove")
    assert resp.status_code == 404


# ---------------------------------------------------------------- add

async def test_add_appends_to_the_end_of_the_rotation(live_sessionmaker):
    session_id, _ = await _make_live_session(live_sessionmaker)  # seat_order 0 already taken
    new_agent_id = await _make_agent(live_sessionmaker)
    async with _client() as client:
        resp = await client.post(f"/sessions/{session_id}/participants", json={"agent_id": str(new_agent_id)})
    assert resp.status_code == 201
    body = resp.json()["session_agent"]
    assert body["seat_order"] == 1
    assert body["muted"] is False
    assert body["removed_at"] is None


async def test_add_honors_an_explicit_seat_order(live_sessionmaker):
    session_id, _ = await _make_live_session(live_sessionmaker)
    new_agent_id = await _make_agent(live_sessionmaker)
    async with _client() as client:
        resp = await client.post(f"/sessions/{session_id}/participants",
                                  json={"agent_id": str(new_agent_id), "seat_order": 99})
    assert resp.status_code == 201
    assert resp.json()["session_agent"]["seat_order"] == 99


async def test_add_404s_for_a_nonexistent_agent(live_sessionmaker):
    session_id, _ = await _make_live_session(live_sessionmaker)
    async with _client() as client:
        resp = await client.post(f"/sessions/{session_id}/participants", json={"agent_id": str(uuid.uuid4())})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"]["field"] == "agent_id"


async def test_add_409s_when_the_agent_is_already_a_participant(live_sessionmaker):
    session_id, agent_id = await _make_live_session(live_sessionmaker)
    async with _client() as client:
        resp = await client.post(f"/sessions/{session_id}/participants", json={"agent_id": str(agent_id)})
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "already_participant"


async def test_re_adding_a_removed_participant_is_already_participant_not_a_silent_unremove(live_sessionmaker):
    """Product-owner-confirmed: explicit beats implicit -- a client who removed someone
    deliberately should never get a surprising silent un-remove from a stale/retried add."""
    session_id, agent_id = await _make_live_session(live_sessionmaker)
    async with _client() as client:
        await client.post(f"/sessions/{session_id}/participants/{agent_id}/remove")
        resp = await client.post(f"/sessions/{session_id}/participants", json={"agent_id": str(agent_id)})
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "already_participant"


async def test_add_409s_when_the_session_is_not_live(live_sessionmaker):
    session_id, _ = await _make_live_session(live_sessionmaker, status="created")
    new_agent_id = await _make_agent(live_sessionmaker)
    async with _client() as client:
        resp = await client.post(f"/sessions/{session_id}/participants", json={"agent_id": str(new_agent_id)})
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "session_not_active"


async def test_add_404s_for_an_unknown_session(live_sessionmaker):
    new_agent_id = await _make_agent(live_sessionmaker)
    async with _client() as client:
        resp = await client.post(f"/sessions/{uuid.uuid4()}/participants", json={"agent_id": str(new_agent_id)})
    assert resp.status_code == 404
