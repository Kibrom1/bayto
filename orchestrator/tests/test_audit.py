"""The audit trail: every change is recorded with its action, the chain detects tampering, the table is append-only,
and nothing sensitive is written into it."""
import json

import httpx
import psycopg
import pytest
from sqlalchemy import select, text

from orchestrator import audit
from orchestrator.app import app
from orchestrator.db import database_url
from orchestrator.models import AuditEvent


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _events(sm, **where):
    async with sm() as db:
        q = select(AuditEvent).order_by(AuditEvent.seq)
        for k, v in where.items():
            q = q.where(getattr(AuditEvent, k) == v)
        return (await db.execute(q)).scalars().all()


async def test_record_chains_each_event_to_the_one_before(live_sessionmaker):
    async with live_sessionmaker() as db:
        a = await audit.record(db, action="x.one", summary="first")
        b = await audit.record(db, action="x.two", summary="second")
        await db.commit()
    assert a.prev_hash == audit.GENESIS and b.prev_hash == a.hash and a.hash != b.hash
    async with live_sessionmaker() as db:
        assert (await audit.verify(db)) == {"ok": True, "checked": 2, "first_bad_seq": None, "head_hash": b.hash}


async def test_a_change_and_its_record_commit_together(live_sessionmaker):
    async with live_sessionmaker() as db:
        await audit.record(db, action="x.rolled_back", summary="never committed")
        await db.rollback()
    assert await _events(live_sessionmaker) == []


def _raw(sql: str):
    with psycopg.connect(database_url().replace("postgresql+psycopg://", "postgresql://", 1), autocommit=True) as conn:
        return conn.execute(sql)


async def test_the_table_refuses_update_delete_and_truncate(live_sessionmaker):
    async with live_sessionmaker() as db:
        await audit.record(db, action="x.one", summary="first")
        await db.commit()
    for sql in ("UPDATE audit_event SET summary = 'edited'", "DELETE FROM audit_event", "TRUNCATE audit_event"):
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            _raw(sql)
    assert len(await _events(live_sessionmaker)) == 1


async def test_verify_finds_an_edited_row_and_a_removed_row(live_sessionmaker):
    async with live_sessionmaker() as db:
        for i in range(4):
            await audit.record(db, action="x.n", summary=f"event {i}", detail={"i": i})
        await db.commit()
    # Someone with database rights switches the guard off and rewrites history.
    _raw("ALTER TABLE audit_event DISABLE TRIGGER audit_event_no_change")
    try:
        _raw("UPDATE audit_event SET summary = 'tampered' WHERE seq = (SELECT min(seq) + 1 FROM audit_event)")
        async with live_sessionmaker() as db:
            bad = await audit.verify(db)
        assert bad["ok"] is False and bad["checked"] == 1
        _raw("UPDATE audit_event SET summary = 'event 1' WHERE summary = 'tampered'")
        async with live_sessionmaker() as db:
            assert (await audit.verify(db))["ok"] is True
        _raw("DELETE FROM audit_event WHERE seq = (SELECT min(seq) + 2 FROM audit_event)")
        async with live_sessionmaker() as db:
            gone = await audit.verify(db)
        assert gone["ok"] is False
    finally:
        _raw("ALTER TABLE audit_event ENABLE TRIGGER audit_event_no_change")


def test_links_are_logged_without_their_query_string():
    assert audit.public_url("https://e.com/doc?token=abc#frag") == "https://e.com/doc"
    assert audit.public_url(None) is None


# ---------------------------------------------------------------- the actions that write to it

AGENT = {"name": "Compliance Officer", "stance": "risk-averse", "instructions": "SECRET-INSTRUCTIONS check the rules."}


async def test_agent_changes_are_recorded_without_the_instructions_text(live_sessionmaker):
    async with _client() as c:
        made = (await c.post("/agents", json=AGENT)).json()
        await c.patch(f"/agents/{made['id']}", json={"stance": "strict"})
        await c.delete(f"/agents/{made['id']}")
    rows = await _events(live_sessionmaker, subject_id=made["id"])
    assert [r.action for r in rows] == ["agent.created", "agent.updated", "agent.archived"]
    assert rows[0].detail["tools"] == [] and rows[0].detail["instructions_chars"] == len(AGENT["instructions"])
    assert rows[1].detail["changed"] == ["stance"]
    assert "SECRET-INSTRUCTIONS" not in json.dumps([r.detail for r in rows] + [r.summary for r in rows])
    assert all(r.actor == "person" for r in rows)


async def test_attachments_are_recorded_by_name_and_fingerprint_not_content(live_sessionmaker):
    async with _client() as c:
        tid = (await c.post("/tasks", json={"title": "T", "brief": "B", "output_type": "memo"})).json()["task"]["id"]
        made = (await c.put(f"/tasks/{tid}/attachments/file", params={"name": "terms.md"}, content=b"CONFIDENTIAL clause 7",
                            headers={"content-type": "application/octet-stream"})).json()
        await c.delete(f"/tasks/{tid}/attachments/{made['id']}")
    rows = await _events(live_sessionmaker, subject_id=made["id"])
    assert [r.action for r in rows] == ["attachment.added", "attachment.removed"]
    assert rows[0].task_id is not None and rows[0].detail["name"] == "terms.md" and len(rows[0].detail["content_sha256"]) == 64
    assert "CONFIDENTIAL" not in json.dumps([r.detail for r in rows])
    tasks = await _events(live_sessionmaker, action="task.created")
    assert tasks and tasks[0].detail["title"] == "T"


async def test_list_filter_verify_and_export_endpoints(live_sessionmaker):
    async with _client() as c:
        a = (await c.post("/agents", json=AGENT)).json()
        await c.post("/agents", json={**AGENT, "name": "Second Agent"})
        await c.delete(f"/agents/{a['id']}")
        everything = (await c.get("/audit")).json()
        assert [e["action"] for e in everything] == ["agent.archived", "agent.created", "agent.created"]  # newest first
        assert len((await c.get("/audit", params={"action": "agent"})).json()) == 3
        assert [e["action"] for e in (await c.get("/audit", params={"action": "agent.archived"})).json()] == ["agent.archived"]
        older = (await c.get("/audit", params={"before": everything[0]["seq"], "limit": 1})).json()
        assert len(older) == 1 and older[0]["seq"] < everything[0]["seq"]
        assert (await c.get("/audit/verify")).json()["ok"] is True
        csv_text = (await c.get("/audit/export")).text
    assert csv_text.splitlines()[0].startswith("seq,at,actor,action") and len(csv_text.splitlines()) == 4


async def test_csv_export_defuses_spreadsheet_formulas(live_sessionmaker):
    async with live_sessionmaker() as db:
        await audit.record(db, action="x.n", summary="=HYPERLINK(\"http://evil\",\"click\")")
        await db.commit()
    async with _client() as c:
        body = (await c.get("/audit/export")).text
    assert "'=HYPERLINK" in body and ",=HYPERLINK" not in body


async def test_there_is_no_way_to_write_to_the_log_through_the_api(live_sessionmaker):
    async with _client() as c:
        for method in ("post", "put", "patch", "delete"):
            r = await getattr(c, method)("/audit")
            assert r.status_code in (404, 405)


async def test_context_changed_inside_a_room_names_the_session(live_sessionmaker):
    from orchestrator.models import Mode, Session
    import uuid
    async with _client() as c:
        tid = (await c.post("/tasks", json={"title": "T", "brief": "B", "output_type": "decision"})).json()["task"]["id"]
        async with live_sessionmaker() as db:
            mode = Mode(name=f"m-{uuid.uuid4().hex[:6]}"); db.add(mode); await db.flush()
            s = Session(task_id=uuid.UUID(tid), mode_id=mode.id, status="active"); db.add(s); await db.commit()
            sid = s.id
        r = await c.put(f"/tasks/{tid}/attachments/file", params={"name": "n.md", "session_id": str(sid)}, content=b"hi",
                        headers={"content-type": "application/octet-stream"})
        await c.delete(f"/tasks/{tid}/attachments/{r.json()['id']}", params={"session_id": str(sid)})
    evs = await _events(live_sessionmaker, session_id=sid)
    assert [e.action for e in evs] == ["attachment.added", "attachment.removed"]
    assert all("during a session" in e.summary for e in evs)
