"""M2.8: POST /tasks. M3.1 adds GET /tasks. Real Postgres (task rows), no sbx or
Anthropic needed."""
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from orchestrator.app import app
from orchestrator.models import Artifact, Mode, Session, Task, UsageLedger


async def test_create_task_persists_and_returns_it(live_sessionmaker):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/tasks", json={
            "title": "Evaluate vendor X", "brief": "Compare X vs Y on cost and latency",
            "output_type": "memo",
        })

    assert resp.status_code == 201
    body = resp.json()["task"]
    assert body["title"] == "Evaluate vendor X"
    assert body["success_criteria"] is None
    assert body["owner_id"] is None
    assert body["created_at"]  # a real timestamp was stamped server-side


async def test_create_task_accepts_optional_fields(live_sessionmaker):
    owner_id = "11111111-1111-1111-1111-111111111111"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/tasks", json={
            "title": "t", "brief": "b", "output_type": "text",
            "success_criteria": "ships a working demo", "owner_id": owner_id,
        })

    assert resp.status_code == 201
    body = resp.json()["task"]
    assert body["success_criteria"] == "ships a working demo"
    assert body["owner_id"] == owner_id


async def test_create_task_rejects_a_missing_required_field(live_sessionmaker):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/tasks", json={"title": "t", "brief": "b"})  # missing output_type

    assert resp.status_code == 422


# ---------------------------------------------------------------- GET /tasks (M3.1)

async def _make_task(sessionmaker) -> Task:
    async with sessionmaker() as db:
        row = Task(title="t", brief="b", output_type="text")
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row


async def _make_mode(sessionmaker, name: str) -> Mode:
    async with sessionmaker() as db:
        row = Mode(name=name, phases_json={}, stop_rules_json={})
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row


async def test_list_tasks_has_a_null_status_and_artifact_for_a_task_with_no_sessions(live_sessionmaker):
    task = await _make_task(live_sessionmaker)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/tasks")

    assert resp.status_code == 200
    [body] = [t for t in resp.json() if t["id"] == str(task.id)]
    assert body["status"] is None
    assert body["last_session_id"] is None
    assert body["output_artifact_id"] is None


async def test_list_tasks_derives_status_from_the_most_recently_started_session(live_sessionmaker):
    task = await _make_task(live_sessionmaker)
    mode = await _make_mode(live_sessionmaker, "m1")
    now = datetime.now(timezone.utc)
    async with live_sessionmaker() as db:
        older = Session(task_id=task.id, mode_id=mode.id, status="finished", started_at=now - timedelta(hours=1))
        newer = Session(task_id=task.id, mode_id=mode.id, status="active", started_at=now)
        db.add_all([older, newer])
        await db.commit()
        await db.refresh(newer)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/tasks")

    [body] = [t for t in resp.json() if t["id"] == str(task.id)]
    assert body["status"] == "active"
    assert body["last_session_id"] == str(newer.id)


async def test_list_tasks_prefers_a_started_session_over_a_never_started_one(live_sessionmaker):
    """The actual NULLS LAST regression guard: a bare `ORDER BY started_at DESC` (Postgres's
    default, NULLS FIRST) would rank the never-started session ahead of the real one here,
    since a fresh `created_at`-less row has no other ordering signal to fall back on."""
    task = await _make_task(live_sessionmaker)
    mode = await _make_mode(live_sessionmaker, "m1b")
    async with live_sessionmaker() as db:
        started = Session(task_id=task.id, mode_id=mode.id, status="active",
                           started_at=datetime.now(timezone.utc))
        never_started = Session(task_id=task.id, mode_id=mode.id, status="created", started_at=None)
        db.add_all([never_started, started])  # inserted in this order on purpose
        await db.commit()
        await db.refresh(started)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/tasks")

    [body] = [t for t in resp.json() if t["id"] == str(task.id)]
    assert body["status"] == "active"
    assert body["last_session_id"] == str(started.id)


async def test_list_tasks_treats_an_unstarted_session_as_last_only_when_its_the_only_one(live_sessionmaker):
    task = await _make_task(live_sessionmaker)
    mode = await _make_mode(live_sessionmaker, "m2")
    async with live_sessionmaker() as db:
        row = Session(task_id=task.id, mode_id=mode.id, status="created", started_at=None)
        db.add(row)
        await db.commit()
        await db.refresh(row)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/tasks")

    [body] = [t for t in resp.json() if t["id"] == str(task.id)]
    assert body["status"] == "created"
    assert body["last_session_id"] == str(row.id)


async def test_list_tasks_links_the_synthesis_artifact_not_a_minority_report(live_sessionmaker):
    task = await _make_task(live_sessionmaker)
    mode = await _make_mode(live_sessionmaker, "m3")
    async with live_sessionmaker() as db:
        session = Session(task_id=task.id, mode_id=mode.id, status="finished",
                           started_at=datetime.now(timezone.utc))
        db.add(session)
        await db.flush()
        synthesis = Artifact(session_id=session.id, type="synthesis", content_json={"ok": True})
        minority = Artifact(session_id=session.id, type="minority_report", content_json={"x": True})
        db.add_all([synthesis, minority])
        await db.commit()
        await db.refresh(synthesis)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/tasks")

    [body] = [t for t in resp.json() if t["id"] == str(task.id)]
    assert body["output_artifact_id"] == str(synthesis.id)


# ---------------------------------------------------------------- GET /tasks/{id}/usage-report (M6)

async def test_usage_report_404s_for_an_unknown_task_id(live_sessionmaker):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/tasks/{uuid.uuid4()}/usage-report")

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"]["code"] == "not_found"


async def test_usage_report_is_zeroed_for_a_task_with_no_sessions(live_sessionmaker):
    task = await _make_task(live_sessionmaker)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/tasks/{task.id}/usage-report")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_tokens_in"] == 0
    assert body["total_tokens_out"] == 0
    assert body["total_cost"] == 0.0
    assert body["ledger"] == []


async def test_usage_report_aggregates_ledger_entries_across_sessions(live_sessionmaker):
    task = await _make_task(live_sessionmaker)
    mode = await _make_mode(live_sessionmaker, "m-usage")
    async with live_sessionmaker() as db:
        session_a = Session(task_id=task.id, mode_id=mode.id, status="finished",
                             started_at=datetime.now(timezone.utc))
        session_b = Session(task_id=task.id, mode_id=mode.id, status="finished",
                             started_at=datetime.now(timezone.utc))
        db.add_all([session_a, session_b])
        await db.flush()
        db.add_all([
            UsageLedger(session_id=session_a.id, tokens_in=100, tokens_out=50, cost=0.01),
            UsageLedger(session_id=session_a.id, tokens_in=200, tokens_out=75, cost=0.02),
            UsageLedger(session_id=session_b.id, tokens_in=300, tokens_out=125, cost=0.03),
        ])
        await db.commit()
        await db.refresh(session_a)
        await db.refresh(session_b)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/tasks/{task.id}/usage-report")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_tokens_in"] == 600
    assert body["total_tokens_out"] == 250
    assert round(body["total_cost"], 2) == 0.06
    assert len(body["ledger"]) == 3
    session_ids = {entry["session_id"] for entry in body["ledger"]}
    assert session_ids == {str(session_a.id), str(session_b.id)}
    for entry in body["ledger"]:
        assert set(entry.keys()) == {"session_id", "tokens_in", "tokens_out", "cost", "timestamp"}
