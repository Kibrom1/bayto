"""Request validation: caller mistakes must come back as 422, never as a 500 or as junk stored in
the database. Found by probing the live API (NUL bytes, repeated roster agents, empty and
multi-megabyte task fields). These all fail before any database access, so no Postgres is needed."""
import uuid

import httpx
import pytest

from orchestrator.api.types import BRIEF_MAX, OUTPUT_TYPE_MAX, TITLE_MAX
from orchestrator.app import app

OK_TASK = {"title": "t", "brief": "b", "output_type": "memo"}


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.parametrize("patch", [
    {"title": ""},
    {"title": "   "},
    {"brief": ""},
    {"output_type": ""},
    {"title": "a\x00b"},
    {"brief": "x\x00"},
    {"title": "a" * (TITLE_MAX + 1)},
    {"brief": "a" * (BRIEF_MAX + 1)},
    {"output_type": "a" * (OUTPUT_TYPE_MAX + 1)},
    {"success_criteria": "a\x00"},
])
async def test_create_task_rejects_bad_fields(client, patch):
    resp = await client.post("/tasks", json={**OK_TASK, **patch})
    assert resp.status_code == 422


def _session_body(roster):
    return {"task_id": str(uuid.uuid4()), "mode_id": str(uuid.uuid4()), "roster": roster}


async def test_create_session_rejects_the_same_agent_twice(client):
    agent = str(uuid.uuid4())
    resp = await client.post("/sessions", json=_session_body(
        [{"agent_id": agent, "seat_order": 0}, {"agent_id": agent, "seat_order": 1}]))
    assert resp.status_code == 422
    assert "same agent" in resp.text


async def test_create_session_rejects_a_repeated_seat(client):
    resp = await client.post("/sessions", json=_session_body(
        [{"agent_id": str(uuid.uuid4()), "seat_order": 0}, {"agent_id": str(uuid.uuid4()), "seat_order": 0}]))
    assert resp.status_code == 422
    assert "seat_order" in resp.text


async def test_create_session_rejects_an_empty_roster(client):
    resp = await client.post("/sessions", json=_session_body([]))
    assert resp.status_code == 422


async def test_create_session_rejects_an_oversized_roster(client):
    resp = await client.post("/sessions", json=_session_body(
        [{"agent_id": str(uuid.uuid4()), "seat_order": i} for i in range(13)]))
    assert resp.status_code == 422


async def test_create_session_rejects_nul_in_a_roster_label(client):
    resp = await client.post("/sessions", json=_session_body(
        [{"agent_id": str(uuid.uuid4()), "model": "claude\x00"}]))
    assert resp.status_code == 422


@pytest.mark.parametrize("body", [{"body": ""}, {"body": "  "}, {"body": "a\x00"}, {"body": "x", "to": ["a\x00"]}])
async def test_interject_rejects_bad_bodies(client, body):
    resp = await client.post(f"/sessions/{uuid.uuid4()}/interject", json=body)
    assert resp.status_code == 422


async def test_stop_rejects_an_oversized_reason(client):
    resp = await client.post(f"/sessions/{uuid.uuid4()}/stop", json={"reason": "r" * 1001})
    assert resp.status_code == 422


async def test_start_without_model_config_is_a_503_that_names_the_variable(client, monkeypatch):
    monkeypatch.delenv("BAYTO_SUMMARY_MODEL", raising=False)
    resp = await client.post(f"/sessions/{uuid.uuid4()}/start")
    assert resp.status_code == 503
    error = resp.json()["detail"]["error"]
    assert error["code"] == "not_configured" and "BAYTO_SUMMARY_MODEL" in error["detail"]
