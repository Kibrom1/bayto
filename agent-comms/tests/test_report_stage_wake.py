import pytest

from acp import Conversation, Roster, FileTransport
from acp.wake import notify, OK, BUSY, BLOCKED, CLAIMED_ELSEWHERE
from acp.cli import main


class FakeWaker:
    def __init__(self, st):
        self.st, self.nudges = st, 0
    def status(self, role):
        return self.st
    def nudge(self, role):
        self.nudges += 1


def mk(tmp_path):
    return Conversation("c1", Roster(roles=["a", "b"]), FileTransport(tmp_path))


def test_ack_is_not_report(tmp_path):
    c = mk(tmp_path)
    m = c.send("a", ["b"], "assignment", "do it")
    c.ack("b", m.message_id)
    assert c.transport.reports() == []
    c.report("b", m.message_id, "pass", {"pytest": 0})
    assert c.transport.reports()[0]["verified"] is True


def test_pass_with_failing_check_rejected(tmp_path):
    c = mk(tmp_path)
    with pytest.raises(ValueError):
        c.report("b", "m1", "pass", {"pytest": 1})
    assert c.report("b", "m1", "fail", {"pytest": 1})["verified"] is False


def test_stage_transitions(tmp_path):
    c = mk(tmp_path)
    assert c.stage() == "open"
    assert c.stage("review") == "review"
    with pytest.raises(ValueError):
        c.stage("bogus")


def test_wake_busy_blocked_and_single_fire(tmp_path):
    t = FileTransport(tmp_path)
    w = FakeWaker("working")
    assert notify(t, w, "b", "m1") == BUSY and w.nudges == 0
    assert notify(t, FakeWaker("blocked"), "b", "m1") == BLOCKED
    w = FakeWaker("idle")
    assert notify(t, w, "b", "m1") == OK and w.nudges == 1
    assert notify(t, w, "b", "m1") == CLAIMED_ELSEWHERE and w.nudges == 1


def test_cli_report_claim_stage(tmp_path, capsys):
    base = ["--dir", str(tmp_path), "--conv", "c", "--role", "b", "--roles", "a,b"]
    assert main(base + ["report", "m1", "--status", "pass", "--check", "pytest=0"]) == 0
    assert main(base + ["report", "m1", "--status", "pass", "--check", "pytest=1"]) == 2
    assert main(base + ["claim", "x"]) == 0
    assert main(base + ["claim", "x"]) == 9
    assert main(base + ["stage", "finished"]) == 0
