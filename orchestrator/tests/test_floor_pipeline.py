"""Build mode B3: PipelineFloorPolicy and parse_verdict -- pure, no I/O."""
import uuid

import pytest

from orchestrator.floor import AskHuman, ConversationView, Done, Grant, PipelineFloorPolicy, parse_verdict

ROUTE = {
    "start": "architect",
    "architect.report.planned": "developer",
    "developer.report.implemented": "qa-tester",
    "qa-tester.report.pass": "reviewer",
    "qa-tester.report.fail": "developer",
    "reviewer.report.approve": "done",
    "reviewer.report.changes": "developer",
}
SEATS = ["architect", "developer", "qa-tester", "reviewer"]


def view(last_speaker=None, verdict=None, turns=None, consecutive=1, muted=()):
    return ConversationView(session_id=uuid.uuid4(), round=0, seat_order=SEATS, last_speaker=last_speaker,
                            last_verdict=verdict, turns_by_role=turns or {}, consecutive_same_speaker=consecutive,
                            muted=set(muted))


def nxt(v, **kw):
    return PipelineFloorPolicy(ROUTE, **kw).next(v, [])


@pytest.mark.parametrize("text,expected", [
    ("done.\nVERDICT: pass", "pass"),
    ("VERDICT: Implemented  ", "implemented"),
    ("**VERDICT: changes**", "changes"),
    ("VERDICT: needs_work", "needs-work"),
    ("early VERDICT: fail\nmore text\nVERDICT: pass", "pass"),  # the last line wins
    ("the verdict is pass", None),
    ("VERDICT:", None),
    ("", None),
    (None, None),
])
def test_parse_verdict(text, expected):
    assert parse_verdict(text) == expected


def test_starts_with_the_start_role():
    assert nxt(view()) == Grant(participant="architect", reason="start")


def test_happy_path_walks_the_pipeline():
    assert nxt(view("architect", "planned")).participant == "developer"
    assert nxt(view("developer", "implemented")).participant == "qa-tester"
    assert nxt(view("qa-tester", "pass")).participant == "reviewer"
    assert nxt(view("reviewer", "approve")) == Done(reason="build-complete")


def test_failures_loop_back_to_the_developer():
    assert nxt(view("qa-tester", "fail", turns={"developer": 1})).participant == "developer"
    assert nxt(view("reviewer", "changes", turns={"developer": 2})).participant == "developer"


def test_fix_cycles_exhausted_asks_a_human():
    # max_fix_cycles=3 allows the first turn plus 3 fixes = 4 developer turns
    assert nxt(view("qa-tester", "fail", turns={"developer": 3})).participant == "developer"
    d = nxt(view("qa-tester", "fail", turns={"developer": 4}))
    assert isinstance(d, AskHuman) and "exhausted" in d.reason


def test_custom_max_fix_cycles():
    d = nxt(view("qa-tester", "fail", turns={"developer": 2}), max_fix_cycles=1)
    assert isinstance(d, AskHuman)


def test_unrouted_verdict_asks_a_human():
    d = nxt(view("developer", "blocked"))
    assert isinstance(d, AskHuman) and "blocked" in d.reason


def test_missing_verdict_repeats_the_speaker_then_asks():
    assert nxt(view("developer", None, consecutive=1)) == Grant(participant="developer", reason="report-verdict")
    d = nxt(view("developer", None, consecutive=2))
    assert isinstance(d, AskHuman) and "VERDICT" in d.reason


def test_missing_or_muted_next_seat_asks_a_human():
    assert isinstance(nxt(view("architect", "planned", muted={"developer"})), AskHuman)
    v = ConversationView(session_id=uuid.uuid4(), round=0, seat_order=["architect"], last_speaker="architect",
                         last_verdict="planned")
    assert isinstance(nxt(v), AskHuman)


def test_route_needs_a_start_entry():
    with pytest.raises(ValueError):
        PipelineFloorPolicy({"architect.report.planned": "developer"})


# ---------------------------------------------------------------- B6: evidence for a pass

from orchestrator.floor import evidence_ok  # noqa: E402

GOOD = "Ran `pytest -q`\nexit code: 0\n12 passed in 1.2s\nVERDICT: pass"


@pytest.mark.parametrize("text,ok", [
    (GOOD, True),
    ("```\n$ npm test\n```\nexited with 0", True),
    ("$ make test\nExit status 0", True),
    ("All tests pass.\nVERDICT: pass", False),                      # no command, no exit code
    ("Ran `pytest`; looks good", False),                            # command but no exit code
    ("exit code: 0 (trust me)", False),                              # exit code but no command
    ("`pytest` exit code: 1\n`pytest -k x` exit code: 0", False),   # any non-zero exit contradicts a pass
    ("", False), (None, False),
])
def test_evidence_ok(text, ok):
    assert evidence_ok(text) is ok


def pol():
    return PipelineFloorPolicy(ROUTE, require_evidence={"qa-tester.report.pass"})


def test_a_bare_pass_is_sent_back_to_the_tester_then_escalated():
    assert pol().next(view("qa-tester", "pass", consecutive=1), []) == Grant(participant="qa-tester", reason="report-evidence")
    d = pol().next(view("qa-tester", "pass", consecutive=2), [])
    assert isinstance(d, AskHuman) and "exit code of 0" in d.reason


def test_a_pass_with_evidence_moves_on():
    v = view("qa-tester", "pass")
    v.last_evidence_ok = True
    assert pol().next(v, []).participant == "reviewer"


def test_a_fail_needs_no_evidence_check():
    assert pol().next(view("qa-tester", "fail", turns={"developer": 1}), []).participant == "developer"
