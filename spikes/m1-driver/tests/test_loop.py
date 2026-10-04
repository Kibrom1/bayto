"""M1.10 spike: floor-loop driver tested purely against the three fakes (SbxLifecycle,
HandRaiseScorer, Summarizer), same test shape/pattern as M2.6/M2.7's tests. agent-comms'
real Conversation/FileTransport is used directly (a tmp_path-backed FileTransport, not
faked -- see loop.py's module docstring) for assignment send/tail/transcript."""
from __future__ import annotations

import pytest
from acp.core import Conversation
from acp.floor import HandRaise, Reason
from acp.models import Roster
from acp.transport import FileTransport

from m1_driver.loop import FloorLoopDriver, RunResult, next_grant
from m1_driver.sbx import FakeSbxLifecycle
from m1_driver.scorer import FakeHandRaiseScorer, PersonaBrief
from m1_driver.summarizer import FakeSummarizer, ModeratorSummary

PERSONAS = [
    PersonaBrief(participant="advocate", role="developer", stance="pro", brief="Argues for the proposal."),
    PersonaBrief(participant="skeptic", role="qa", stance="con", brief="Argues against the proposal."),
]

CANNED_SUMMARY = ModeratorSummary(
    strongest_points={"advocate": "Ships faster.", "skeptic": "Riskier rollback."},
    agreement="Both want a staged rollout.",
    disagreement="Whether the risk is acceptable for v1.",
    recommendation="Ship behind a flag.",
)


def mk_conversation(tmp_path) -> Conversation:
    transport = FileTransport(tmp_path / "factory")
    return Conversation("m1-driver-test", Roster(roles=["*"]), transport)


# --------------------------------------------------------------- next_grant (pure)

def test_next_grant_picks_the_highest_urgency_non_agree_pass():
    raised = [
        HandRaise(participant="advocate", reason=Reason.NEW_POINT, urgency=0.4),
        HandRaise(participant="skeptic", reason=Reason.DISAGREE, urgency=0.8),
        HandRaise(participant="moderator", reason=Reason.AGREE_PASS, urgency=0.99),
    ]
    assert next_grant(raised).participant == "skeptic"


def test_next_grant_is_none_when_everything_is_agree_pass():
    raised = [HandRaise(participant="advocate", reason=Reason.AGREE_PASS, urgency=0.9)]
    assert next_grant(raised) is None


def test_next_grant_is_none_when_nothing_is_raised():
    assert next_grant([]) is None


# --------------------------------------------------------------- full loop (fakes + real acp)

async def test_converges_after_k_quiet_iterations_and_removes_the_sandbox(tmp_path):
    sbx = FakeSbxLifecycle()
    scorer = FakeHandRaiseScorer([
        [HandRaise(participant="advocate", reason=Reason.NEW_POINT, urgency=0.7)],
        [HandRaise(participant="skeptic", reason=Reason.AGREE_PASS, urgency=0.2)],
        [],
    ])
    summarizer = FakeSummarizer(CANNED_SUMMARY)
    driver = FloorLoopDriver(
        sbx=sbx, scorer=scorer, summarizer=summarizer,
        conversation=mk_conversation(tmp_path), converge_after_quiet_iterations=2,
    )

    result = await driver.run(sandbox_name="m1-driver-spike", personas=PERSONAS)

    assert isinstance(result, RunResult)
    assert result.converged is True
    assert result.iterations == 3
    assert result.grants == ["advocate"]
    assert result.summary == CANNED_SUMMARY
    assert sbx.calls == [
        ("create", "m1-driver-spike"),
        ("start_team", "m1-driver-spike"),
        ("crew_notify", "m1-driver-spike", "advocate"),
        ("remove", "m1-driver-spike"),
    ]


async def test_sends_the_grant_and_final_summary_as_real_envelopes(tmp_path):
    conversation = mk_conversation(tmp_path)
    scorer = FakeHandRaiseScorer([
        [HandRaise(participant="advocate", reason=Reason.DISAGREE, urgency=0.9)],
        [],
        [],
    ])
    driver = FloorLoopDriver(
        sbx=FakeSbxLifecycle(), scorer=scorer, summarizer=FakeSummarizer(CANNED_SUMMARY),
        conversation=conversation, converge_after_quiet_iterations=2,
    )

    await driver.run(sandbox_name="m1-driver-spike", personas=PERSONAS)

    transcript = conversation.transcript("human")
    kinds = [e.kind for e in transcript]
    assert "assignment" in kinds
    assert kinds[-1] == "summary"
    assignment = next(e for e in transcript if e.kind == "assignment")
    assert assignment.to == ["advocate"]
    summary_envelope = next(e for e in transcript if e.kind == "summary")
    assert summary_envelope.to == ["human"]
    assert "Ships faster." in summary_envelope.body


async def test_removes_the_sandbox_even_if_the_scorer_raises(tmp_path):
    class BoomScorer:
        async def score(self, view, personas):
            raise RuntimeError("boom")

    sbx = FakeSbxLifecycle()
    driver = FloorLoopDriver(
        sbx=sbx, scorer=BoomScorer(), summarizer=FakeSummarizer(CANNED_SUMMARY),
        conversation=mk_conversation(tmp_path),
    )

    with pytest.raises(RuntimeError):
        await driver.run(sandbox_name="m1-driver-spike", personas=PERSONAS)

    assert sbx.calls[-1] == ("remove", "m1-driver-spike")


async def test_stops_at_max_iterations_if_never_converging(tmp_path):
    sbx = FakeSbxLifecycle()
    # Always a fresh (non-AGREE_PASS) raise -- never quiets down.
    scorer = FakeHandRaiseScorer(
        [[HandRaise(participant="advocate", reason=Reason.NEW_POINT, urgency=0.5)]] * 5
    )
    driver = FloorLoopDriver(
        sbx=sbx, scorer=scorer, summarizer=FakeSummarizer(CANNED_SUMMARY),
        conversation=mk_conversation(tmp_path), max_iterations=5,
    )

    result = await driver.run(sandbox_name="m1-driver-spike", personas=PERSONAS)

    assert result.converged is False
    assert result.iterations == 5
