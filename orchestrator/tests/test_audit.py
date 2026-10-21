"""The audit trail is emitted as OpenTelemetry events: every action is recorded with who did it, nothing sensitive is
written, and nothing is stored in Bayto's own database."""
import json
import logging

import httpx
from sqlalchemy import text

from audit_helpers import audit_events
from orchestrator import audit
from orchestrator.app import app


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_emit_creates_an_audit_span_and_a_log_line(span_exporter, caplog):
    with caplog.at_level(logging.INFO, logger="bayto.audit"):
        audit.emit(action="x.one", summary="first", detail={"n": 1}, subject_type="thing", subject_id=7)
    [ev] = audit_events(span_exporter)
    assert (ev.action, ev.actor, ev.summary, ev.subject_type, ev.subject_id, ev.detail) == \
           ("x.one", "person", "first", "thing", "7", {"n": 1})
    line = json.loads(caplog.records[-1].getMessage())
    assert line["action"] == "x.one" and line["detail"] == '{"n": 1}'


def test_emit_never_raises(span_exporter, monkeypatch):
    monkeypatch.setattr(audit, "_tracer", None)  # anything that breaks inside must not reach the caller
    audit.emit(action="x.broken", summary="still fine")


def test_links_are_logged_without_their_query_string():
    assert audit.public_url("https://e.com/doc?token=abc#frag") == "https://e.com/doc"
    assert audit.public_url(None) is None


async def test_nothing_is_stored_in_the_database(live_sessionmaker):
    async with live_sessionmaker() as db:
        found = (await db.execute(text("SELECT to_regclass('audit_event')"))).scalar()
    assert found is None
    async with _client() as c:
        assert (await c.get("/audit")).status_code == 404


AGENT = {"name": "Compliance Officer", "stance": "risk-averse", "instructions": "SECRET-INSTRUCTIONS check the rules."}


async def test_agent_changes_are_recorded_without_the_instructions_text(live_sessionmaker, span_exporter):
    async with _client() as c:
        made = (await c.post("/agents", json=AGENT)).json()
        await c.patch(f"/agents/{made['id']}", json={"stance": "strict"})
        await c.delete(f"/agents/{made['id']}")
    rows = audit_events(span_exporter, subject_id=made["id"])
    assert [r.action for r in rows] == ["agent.created", "agent.updated", "agent.archived"]
    assert rows[0].detail["tools"] == [] and rows[0].detail["instructions_chars"] == len(AGENT["instructions"])
    assert rows[1].detail["changed"] == ["stance"]
    assert "SECRET-INSTRUCTIONS" not in json.dumps([r.detail for r in rows] + [r.summary for r in rows])
    assert all(r.actor == "person" for r in rows)


async def test_attachments_are_recorded_by_name_and_fingerprint_not_content(live_sessionmaker, span_exporter):
    async with _client() as c:
        tid = (await c.post("/tasks", json={"title": "T", "brief": "B", "output_type": "memo"})).json()["task"]["id"]
        made = (await c.put(f"/tasks/{tid}/attachments/file", params={"name": "terms.md"}, content=b"CONFIDENTIAL clause 7",
                            headers={"content-type": "application/octet-stream"})).json()
        await c.delete(f"/tasks/{tid}/attachments/{made['id']}")
    rows = audit_events(span_exporter, subject_id=made["id"])
    assert [r.action for r in rows] == ["attachment.added", "attachment.removed"]
    assert rows[0].task_id == tid and rows[0].detail["name"] == "terms.md" and len(rows[0].detail["content_sha256"]) == 64
    assert "CONFIDENTIAL" not in json.dumps([r.detail for r in rows])
    [task] = audit_events(span_exporter, action="task.created")
    assert task.detail["title"] == "T"


async def test_context_changed_inside_a_room_names_the_session(live_sessionmaker, span_exporter):
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
    evs = audit_events(span_exporter, session_id=sid)
    assert [e.action for e in evs] == ["attachment.added", "attachment.removed"]
    assert all("during a session" in e.summary for e in evs)
