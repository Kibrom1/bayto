"""M2.3: file-watcher-style mirroring of an acp FileTransport into Postgres.

`test_pubsub_fanout_in_order` needs no DB. The rest exercise ordering, resume-after-restart
and idempotency against a real, throwaway Postgres (see conftest.py's `live_sessionmaker`
fixture, which skips cleanly when none is reachable).
"""
from sqlalchemy import select

from acp.models import Envelope
from acp.transport import FileTransport
from orchestrator.models import Message
from orchestrator.mirror import MessageMirror
from orchestrator.pubsub import PubSub


def test_pubsub_fanout_in_order():
    pubsub = PubSub()
    q = pubsub.subscribe()
    for i in range(3):
        pubsub.publish({"type": "message", "data": {"seq": i}})
    got = [q.get_nowait() for _ in range(3)]
    assert [g["data"]["seq"] for g in got] == [0, 1, 2]


def test_pubsub_fanout_to_multiple_concurrent_subscribers():
    pubsub = PubSub()
    q1 = pubsub.subscribe()
    q2 = pubsub.subscribe()
    q3 = pubsub.subscribe()
    for i in range(3):
        pubsub.publish({"type": "message", "data": {"seq": i}})

    for q in (q1, q2, q3):
        got = [q.get_nowait() for _ in range(3)]
        assert [g["data"]["seq"] for g in got] == [0, 1, 2]
        assert q.empty()

    # Unsubscribing one queue doesn't affect delivery to the others.
    pubsub.unsubscribe(q2)
    pubsub.publish({"type": "message", "data": {"seq": 3}})
    assert q1.get_nowait()["data"]["seq"] == 3
    assert q3.get_nowait()["data"]["seq"] == 3
    assert q2.empty()


def _send(transport: FileTransport, body: str, **kw) -> Envelope:
    return transport.send(Envelope(conversation_id="c1", from_="coordinator",
                                    to=["backend-engineer"], kind="note", body=body, **kw))


async def test_mirror_orders_resumes_and_is_idempotent(tmp_path, live_sessionmaker):
    transport = FileTransport(tmp_path)
    for i in range(4):
        _send(transport, f"m{i}")

    pubsub = PubSub()
    q = pubsub.subscribe()
    mirror = MessageMirror(transport, live_sessionmaker, pubsub)

    result = await mirror.poll_once()
    assert [e.seq for e in result.new_messages] == [1, 2, 3, 4]
    assert [e.body for e in result.new_messages] == ["m0", "m1", "m2", "m3"]

    published = [q.get_nowait() for _ in range(4)]
    assert [p["data"]["seq"] for p in published] == [1, 2, 3, 4]
    assert q.empty()

    # Idempotency: re-polling the exact same files creates no new rows and no new events.
    result2 = await mirror.poll_once()
    assert result2.new_messages == []
    assert q.empty()

    async with live_sessionmaker() as db:
        rows = (await db.execute(select(Message.seq).order_by(Message.seq))).scalars().all()
    assert rows == [1, 2, 3, 4]

    # Resume after "restart": a brand-new MessageMirror instance (simulating a fresh
    # orchestrator process) against the same transport only mirrors what's genuinely new.
    for i in range(4, 6):
        _send(transport, f"m{i}")
    restarted = MessageMirror(transport, live_sessionmaker, pubsub)
    result3 = await restarted.poll_once()
    assert [e.seq for e in result3.new_messages] == [5, 6]

    async with live_sessionmaker() as db:
        rows = (await db.execute(select(Message.seq).order_by(Message.seq))).scalars().all()
    assert rows == [1, 2, 3, 4, 5, 6]


async def test_report_deferred_until_its_message_is_mirrored(tmp_path, live_sessionmaker):
    transport = FileTransport(tmp_path)
    # A report can reference a message this mirror hasn't seen yet (e.g. a different poll
    # cycle, or -- as here -- simply not sent yet); report.message_id is a real FK, so it
    # must not be inserted until the message row exists.
    transport.report("qa-tester", "not-yet-sent", "review-pass", checks={"pytest": 0})

    mirror = MessageMirror(transport, live_sessionmaker)
    result = await mirror.poll_once()
    assert result.new_reports == []
    assert [r["message_id"] for r in result.deferred_reports] == ["not-yet-sent"]

    _send(transport, "the assignment", message_id="not-yet-sent")
    result2 = await mirror.poll_once()
    assert [(r["message_id"], r["status"]) for r in result2.new_reports] == [("not-yet-sent", "review-pass")]
    assert result2.deferred_reports == []

    # Idempotency for reports too.
    result3 = await mirror.poll_once()
    assert result3.new_reports == []
