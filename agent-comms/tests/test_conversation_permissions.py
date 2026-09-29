"""M2.5 integration tests against a real tmp_path factory dir (FileTransport), same
fixture pattern as test_file_transport.py: send-kind permissions (SendPolicy) layered on
top of the existing recipient permissions (Roster), and the viewer-scoped
Conversation.transcript() fix. No live Postgres or sbx needed."""
import pytest

from acp import Conversation, FileTransport, PermissionError_, Roster, SendPolicy

ROLES = ["coordinator", "developer", "qa"]


def mk(tmp_path, *, send_policy=None, moderator_role=None, roster_kw=None):
    return Conversation("c1", Roster(roles=ROLES, **(roster_kw or {})), FileTransport(tmp_path),
                         send_policy=send_policy, moderator_role=moderator_role)


def test_no_send_policy_is_byte_for_byte_unchanged(tmp_path):
    """Every existing caller (in-sandbox acp CLI etc.) passes no send_policy; behavior
    must be exactly what it was before this task."""
    c = mk(tmp_path)
    c.send("developer", ["qa"], "note", "hi")


def test_send_policy_rejects_a_disallowed_kind(tmp_path):
    policy = SendPolicy(may_send={"developer": ["report", "note"]})
    c = mk(tmp_path, send_policy=policy)
    with pytest.raises(PermissionError_):
        c.send("developer", ["qa"], "assignment", "go")


def test_send_policy_allows_a_listed_kind(tmp_path):
    policy = SendPolicy(may_send={"developer": ["report", "note"]})
    c = mk(tmp_path, send_policy=policy)
    c.send("developer", ["qa"], "report", "done")


def test_send_policy_and_roster_are_both_enforced(tmp_path):
    """Roster.can_send (recipients) and SendPolicy.check (kind) are independent axes;
    a message must pass both."""
    policy = SendPolicy(may_send={"developer": ["report"]})
    c = mk(tmp_path, send_policy=policy, roster_kw={"send": {"developer": ["qa"]}})
    with pytest.raises(PermissionError_):  # wrong recipient
        c.send("developer", ["coordinator"], "report", "done")
    with pytest.raises(PermissionError_):  # wrong kind
        c.send("developer", ["qa"], "note", "hi")
    c.send("developer", ["qa"], "report", "done")  # both satisfied


def test_role_unrestricted_when_mapped_to_none(tmp_path):
    policy = SendPolicy(may_send={"coordinator": None, "developer": ["report"]})
    c = mk(tmp_path, send_policy=policy)
    c.send("coordinator", ["developer"], "assignment", "go")
    c.send("coordinator", ["developer"], "summary", "wrap-up")


def test_transcript_all_visible_to_everyone(tmp_path):
    c = mk(tmp_path)
    c.send("coordinator", ["developer"], "assignment", "go")
    assert [e.kind for e in c.transcript("developer")] == ["assignment"]
    assert [e.kind for e in c.transcript("qa")] == ["assignment"]
    assert [e.kind for e in c.transcript("stranger")] == ["assignment"]


def test_transcript_recipients_hidden_from_non_recipients(tmp_path):
    c = mk(tmp_path)
    c.send("coordinator", ["developer"], "note", "psst", visibility="recipients")
    assert [e.body for e in c.transcript("coordinator")] == ["psst"]
    assert [e.body for e in c.transcript("developer")] == ["psst"]
    assert c.transcript("qa") == []


def test_transcript_moderator_hidden_from_non_moderator(tmp_path):
    c = mk(tmp_path, moderator_role="coordinator")
    c.send("developer", ["qa"], "note", "confidential", visibility="moderator")
    assert [e.body for e in c.transcript("developer")] == ["confidential"]  # sender
    assert [e.body for e in c.transcript("coordinator")] == ["confidential"]  # moderator
    assert c.transcript("qa") == []  # recipient, but not the moderator


def test_transcript_moderator_visibility_matches_nobody_but_sender_when_unset(tmp_path):
    c = mk(tmp_path)  # no moderator_role
    c.send("developer", ["qa"], "note", "confidential", visibility="moderator")
    assert [e.body for e in c.transcript("developer")] == ["confidential"]
    assert c.transcript("qa") == []
    assert c.transcript("coordinator") == []


def test_transcript_mixed_visibility_is_filtered_independently_per_viewer(tmp_path):
    c = mk(tmp_path, moderator_role="coordinator")
    c.send("coordinator", ["*"], "summary", "public", visibility="all")
    c.send("developer", ["qa"], "note", "side-channel", visibility="recipients")
    c.send("qa", ["coordinator"], "note", "for-mod-only", visibility="moderator")

    assert [e.body for e in c.transcript("coordinator")] == ["public", "for-mod-only"]
    assert [e.body for e in c.transcript("developer")] == ["public", "side-channel"]
    assert [e.body for e in c.transcript("qa")] == ["public", "side-channel", "for-mod-only"]
