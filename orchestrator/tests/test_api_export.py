"""GET /sessions/{id}/export: the Markdown decision record."""
import uuid
from datetime import datetime, timezone

import httpx
import pytest

from orchestrator.app import app
from orchestrator.models import Agent, Artifact, Message, Mode, Session, SessionAgent, Task, TaskAttachment, Turn


@pytest.fixture
async def client(live_sessionmaker):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _msg(sid, seq, frm, body, kind="answer", visibility="all", meta=None, to=("*",)):
    return Message(message_id=f"m-{uuid.uuid4().hex}", session_id=sid, conversation_id=str(sid), seq=seq, from_=frm,
                   to=list(to), visibility=visibility, kind=kind, body=body, meta=meta or {},
                   created_at=datetime(2026, 10, 23, 12, seq, tzinfo=timezone.utc))


async def _seed(sm, *, status="finished", synthesis=True):
    async with sm() as db:
        task = Task(title="Pick a DB: Postgres?", brief="Why?\n```\n# not a heading\n```", output_type="decision")
        mode = Mode(name=f"debate-{uuid.uuid4().hex[:6]}", phases_json={}, stop_rules_json={})
        a = Agent(kind="native", name=f"alice-{uuid.uuid4().hex[:4]}", role="architect", model="claude-x")
        db.add_all([task, mode, a]); await db.flush()
        s = Session(task_id=task.id, mode_id=mode.id, status=status, stop_reason="converged",
                    started_at=datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc))
        db.add(s); await db.flush()
        db.add(SessionAgent(session_id=s.id, agent_id=a.id, seat_order=1, model="claude-y"))
        db.add(TaskAttachment(task_id=task.id, kind="file", name="secret.md", content="TOPSECRET TEXT", roles=["architect"]))
        db.add(Turn(session_id=s.id, seq=1, speaker_id=a.id, content="x", tokens_in=10, tokens_out=5, cost=0.25))
        if synthesis:
            db.add_all([Artifact(session_id=s.id, type="synthesis", content_json={"recommendation": "Use Postgres\n# injected"}, source_turn_ids=[]),
                        Artifact(session_id=s.id, type="minority_report", content_json={"objection": "too heavy"}, source_turn_ids=[]),
                        Artifact(session_id=s.id, type="action_items", content_json={"items": ["migrate"]}, source_turn_ids=[])])
        db.add_all([
            _msg(s.id, 1, a.name, "I think ``` break ````` out", meta={"attachments": [{"name": "secret.md", "mode": "whole"}]}),
            _msg(s.id, 2, "human", "please consider cost"),
            _msg(s.id, 3, a.name, "PRIVATE-NOTE", visibility="recipients"),
            _msg(s.id, 4, "moderator", "ASSIGNMENT-PROMPT", kind="assignment"),
        ])
        await db.commit()
        return s.id


async def test_export_has_all_sections_and_headers(client, live_sessionmaker):
    sid = await _seed(live_sessionmaker)
    r = await client.get(f"/sessions/{sid}/export", params={"format": "md"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment;") and "bayto-pick-a-db-postgres-" in cd and cd.endswith('.md"')
    md = r.text
    for h in ("# Decision record: Pick a DB: Postgres?", "## Question", "## Session", "## Roster", "## Synthesis",
              "## Minority report", "## Decisions and action items", "## Attachments read", "## Cost and tokens", "## Transcript"):
        assert h in md
    assert "Stop reason: converged" in md and "claude-y" in md and "architect" in md
    assert "Total tokens: 15" in md and "$0.2500" in md
    assert "| secret.md | only architect |" in md
    assert "TOPSECRET" not in md
    assert "(human interjection)" in md and "please consider cost" in md
    assert "Unfinished" not in md


async def test_export_filters_visibility_and_internal_messages(client, live_sessionmaker):
    sid = await _seed(live_sessionmaker)
    md = (await client.get(f"/sessions/{sid}/export")).text
    assert "PRIVATE-NOTE" not in md and "ASSIGNMENT-PROMPT" not in md


async def test_export_text_cannot_break_structure(client, live_sessionmaker):
    sid = await _seed(live_sessionmaker)
    md = (await client.get(f"/sessions/{sid}/export")).text
    # the message with a 5-backtick run is fenced by a longer fence; the brief's 3-backtick run by 4
    assert "``````\nI think ``` break ````` out\n``````" in md
    assert "````\nWhy?\n```\n# not a heading\n```\n````" in md
    assert "> # injected" in md and "\n# injected" not in md


async def test_export_404_and_bad_format(client, live_sessionmaker):
    assert (await client.get(f"/sessions/{uuid.uuid4()}/export")).status_code == 404
    sid = await _seed(live_sessionmaker)
    assert (await client.get(f"/sessions/{sid}/export", params={"format": "pdf"})).status_code == 422


async def test_unfinished_session_still_exports_with_a_note(client, live_sessionmaker):
    sid = await _seed(live_sessionmaker, status="active", synthesis=False)
    r = await client.get(f"/sessions/{sid}/export")
    assert r.status_code == 200
    assert "Unfinished session" in r.text and "No synthesis was written" in r.text
