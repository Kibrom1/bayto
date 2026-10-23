"""GET /search: ranking, visibility, archived filter, operator-laden queries, migration."""
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import sqlalchemy as sa
from alembic import command

from orchestrator.app import app
from orchestrator.db import database_url
from orchestrator.models import Artifact, Message, Mode, Session, Task

WHEN = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _task(sm, title="Vendor contract"):
    async with sm() as db:
        t = Task(title=title, output_type="memo", brief="b")
        m = Mode(name=f"m-{uuid.uuid4().hex[:6]}")
        db.add_all([t, m]); await db.commit()
        return t.id, m.id


async def _session(sm, task_id, mode_id, messages=(), synthesis=None, archived=False):
    async with sm() as db:
        s = Session(task_id=task_id, mode_id=mode_id, status="finished", started_at=WHEN,
                    archived_at=WHEN if archived else None)
        db.add(s); await db.flush()
        for n, (who, kind, body, vis) in enumerate(messages, 1):
            db.add(Message(message_id=f"m-{s.id}-{n}", session_id=s.id, conversation_id=f"c-{s.id}", seq=n,
                           from_=who, to=["*"], kind=kind, body=body, visibility=vis,
                           created_at=WHEN + timedelta(minutes=n)))
        if synthesis:
            db.add(Artifact(session_id=s.id, type="synthesis", content_json=synthesis, source_turn_ids=[]))
        await db.commit()
        return s.id


async def _get(**params):
    async with _client() as c:
        return await c.get("/search", params=params)


async def test_ranking_and_shape(live_sessionmaker):
    tid, mid = await _task(live_sessionmaker)
    sid = await _session(live_sessionmaker, tid, mid, messages=[
        ("lawyer", "answer", "The indemnity clause is uncapped. Indemnity matters; push for indemnity limits.", "all"),
        ("architect", "answer", "One passing mention of indemnity in a long text about databases and replicas.", "all"),
    ])
    r = await _get(q="indemnity", task_id=str(tid))
    assert r.status_code == 200
    res = r.json()["results"]
    assert [h["speaker"] for h in res] == ["lawyer", "architect"]
    assert res[0]["rank"] >= res[1]["rank"]
    h = res[0]
    assert h["task_id"] == str(tid) and h["task_title"] == "Vendor contract" and h["session_id"] == str(sid)
    assert h["message_id"].startswith("m-") and "[[" in h["snippet"] and "]]" in h["snippet"]
    assert h["created_at"]


async def test_synthesis_is_searched(live_sessionmaker):
    tid, mid = await _task(live_sessionmaker)
    await _session(live_sessionmaker, tid, mid, synthesis={"decision": "Adopt a message queue", "dissent": ["cron is simpler"]})
    res = (await _get(q="queue", task_id=str(tid))).json()["results"]
    assert len(res) == 1 and res[0]["speaker"] == "synthesis" and res[0]["message_id"] is None
    assert "[[queue]]" in res[0]["snippet"]


async def test_only_visibility_all(live_sessionmaker):
    tid, mid = await _task(live_sessionmaker)
    await _session(live_sessionmaker, tid, mid, messages=[
        ("a", "answer", "secret zebra plan", "moderator"), ("b", "answer", "recipient zebra plan", "recipients"),
        ("c", "answer", "public zebra plan", "all")])
    res = (await _get(q="zebra", task_id=str(tid))).json()["results"]
    assert [h["speaker"] for h in res] == ["c"]


async def test_archived_filter(live_sessionmaker):
    tid, mid = await _task(live_sessionmaker)
    await _session(live_sessionmaker, tid, mid, messages=[("a", "answer", "old giraffe note", "all")], archived=True)
    assert (await _get(q="giraffe", task_id=str(tid))).json()["results"] == []
    res = (await _get(q="giraffe", task_id=str(tid), include_archived="true")).json()["results"]
    assert len(res) == 1


async def test_task_filter(live_sessionmaker):
    t1, mid = await _task(live_sessionmaker, "One")
    t2, _ = await _task(live_sessionmaker, "Two")
    await _session(live_sessionmaker, t1, mid, messages=[("a", "answer", "walrus one", "all")])
    await _session(live_sessionmaker, t2, mid, messages=[("a", "answer", "walrus two", "all")])
    assert len((await _get(q="walrus")).json()["results"]) == 2
    res = (await _get(q="walrus", task_id=str(t2))).json()["results"]
    assert [h["task_title"] for h in res] == ["Two"]


@pytest.mark.parametrize("q", ['"unclosed', "a < b", "foo:bar", "x & y", "x | y", "!!", "'; drop table message;--", "&&", "<->"])
async def test_operator_laden_queries_do_not_error(live_sessionmaker, q):
    tid, mid = await _task(live_sessionmaker)
    await _session(live_sessionmaker, tid, mid, messages=[("a", "answer", "foo and bar together", "all")])
    r = await _get(q=q)
    assert r.status_code == 200


async def test_operators_are_plain_text(live_sessionmaker):
    tid, mid = await _task(live_sessionmaker)
    await _session(live_sessionmaker, tid, mid, messages=[("a", "answer", "foo and bar together", "all")])
    res = (await _get(q='"foo bar" | baz:*', task_id=str(tid))).json()["results"]
    assert res == [] or all(isinstance(h["snippet"], str) for h in res)
    assert len((await _get(q="foo & bar", task_id=str(tid))).json()["results"]) == 1


async def test_existing_markers_and_html_stay_plain(live_sessionmaker):
    tid, mid = await _task(live_sessionmaker)
    await _session(live_sessionmaker, tid, mid, messages=[("a", "answer", "see [[fake]] <b>pelican</b> here", "all")])
    s = (await _get(q="pelican", task_id=str(tid))).json()["results"][0]["snippet"]
    assert "[ [fake] ]" in s and "[[pelican]]" in s and "<b>" not in s  # the text parser drops tags; the web also renders snippets as text


@pytest.mark.parametrize("params", [{"q": ""}, {"q": " a "}, {"q": "x" * 201}, {"q": "ok", "limit": 0}, {}])
async def test_bad_query_is_422(live_sessionmaker, params):
    assert (await _get(**params)).status_code == 422


async def test_limit_is_capped(live_sessionmaker):
    tid, mid = await _task(live_sessionmaker)
    await _session(live_sessionmaker, tid, mid, messages=[("a", "answer", f"quokka {i}", "all") for i in range(60)])
    assert len((await _get(q="quokka", limit=1000)).json()["results"]) == 50
    assert len((await _get(q="quokka", limit=3)).json()["results"]) == 3


def test_migration_up_down(live_schema):
    eng = sa.create_engine(database_url())
    def idx():
        with eng.connect() as c:
            return {r[0] for r in c.execute(sa.text("select indexname from pg_indexes where indexname like 'ix_%_fts'"))}
    assert idx() == {"ix_message_body_fts", "ix_artifact_content_fts"}
    command.downgrade(live_schema, "d0e1f2a3b4c5")
    assert idx() == set()
    command.upgrade(live_schema, "head")
    assert len(idx()) == 2
    eng.dispose()
