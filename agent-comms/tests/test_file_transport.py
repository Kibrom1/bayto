import multiprocessing as mp

import pytest

from acp import Conversation, Roster, FileTransport, PermissionError_
from acp.models import Envelope

ROLES = ["moderator", "advocate", "skeptic"]


def mk(tmp_path, **kw):
    return Conversation("c1", Roster(roles=ROLES, **kw), FileTransport(tmp_path))


def _send_n(root, who, n):
    t = FileTransport(root)
    for i in range(n):
        t.send(Envelope(conversation_id="c1", from_=who, to=["*"], kind="note", body=str(i)))


def test_concurrent_seq_unique_and_gapless(tmp_path):
    ps = [mp.Process(target=_send_n, args=(tmp_path, w, 25)) for w in ROLES]
    [p.start() for p in ps]
    [p.join() for p in ps]
    seqs = [e.seq for e in FileTransport(tmp_path).all()]
    assert seqs == list(range(1, 76))


def test_messages_after_returns_only_newer_in_seq_order(tmp_path):
    t = FileTransport(tmp_path)
    for i in range(5):
        t.send(Envelope(conversation_id="c1", from_="moderator", to=["*"], kind="note", body=str(i)))
    assert [e.seq for e in t.messages_after(0)] == [1, 2, 3, 4, 5]
    assert [e.seq for e in t.messages_after(3)] == [4, 5]
    assert t.messages_after(5) == []


def test_redelivery_until_ack(tmp_path):
    c = mk(tmp_path)
    m = c.send("moderator", ["advocate"], "assignment", "go")
    assert [e.message_id for e in c.read("advocate")] == [m.message_id]
    # crash before consume: still delivered
    assert len(c.read("advocate")) == 1
    c.ack("advocate", m.message_id)
    assert c.read("advocate") == []


def test_broadcast_consumed_per_recipient(tmp_path):
    c = mk(tmp_path)
    m = c.send("moderator", ["*"], "note", "hi")
    c.ack("advocate", m.message_id)
    assert c.read("advocate") == []
    assert len(c.read("skeptic")) == 1
    assert c.read("moderator") == []  # own send not delivered back


def test_stale_attempt_ignored(tmp_path):
    c = mk(tmp_path)
    c.send("moderator", ["advocate"], "note", "old")
    c2 = Conversation("c1", Roster(roles=ROLES), c.transport, attempt=2)
    assert c2.read("advocate") == []


def test_permission_rejected(tmp_path):
    c = mk(tmp_path, send={"skeptic": ["moderator"]})
    with pytest.raises(PermissionError_):
        c.send("skeptic", ["advocate"], "critique", "x")
    c.send("skeptic", ["moderator"], "critique", "ok")
    with pytest.raises(PermissionError_):
        c.send("stranger", ["moderator"], "note", "x")


def test_unknown_kind_rejected(tmp_path):
    c = mk(tmp_path)
    with pytest.raises(ValueError):
        c.send("moderator", ["*"], "gossip", "x")
    c.send("moderator", ["*"], "x-poll", "ok")


def test_single_fire_claim(tmp_path):
    t = FileTransport(tmp_path)
    assert t.claim("wake-1") is True
    assert t.claim("wake-1") is False


BAD_NAMES = ["", ".", "..", "../x", "a/b", "a\\b", "a\x00b"]


def _env(message_id):
    return Envelope(conversation_id="c1", from_="moderator", to=["*"], kind="note", message_id=message_id)


@pytest.mark.parametrize("bad", BAD_NAMES)
def test_unsafe_names_rejected_without_writes(tmp_path, bad):
    t = FileTransport(tmp_path / "root")
    calls = [
        lambda: t.send(_env(bad)),
        lambda: t.ack(bad, "m"),
        lambda: t.ack("r", bad),
        lambda: t.claim(bad),
        lambda: t.report(bad, "m", "implemented"),
        lambda: t.report("r", bad, "implemented"),
    ]
    for call in calls:
        before = sorted(tmp_path.rglob("*"))
        with pytest.raises(ValueError):  # pydantic ValidationError is a ValueError too
            call()
        assert sorted(tmp_path.rglob("*")) == before


def test_normal_names_still_work(tmp_path):
    t = FileTransport(tmp_path)
    for name in ("build-architect", "0123456789abcdef0123456789abcdef", "wake-human-abc"):
        e = t.send(_env(name))
        assert [m.message_id for m in t.read("advocate") if m.message_id == name] == [e.message_id]
        t.ack("advocate", name)
        assert all(m.message_id != name for m in t.read("advocate"))
        t.report(name, name, "implemented")
        assert t.claim(name) is True
