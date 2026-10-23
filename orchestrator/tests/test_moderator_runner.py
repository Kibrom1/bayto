"""M2.7: ModeratorRunner.run_once() -- the tested unit (run() itself, like
MessageMirror.run_forever(), isn't exercised directly). Uses a real throwaway Postgres
(DB rows: task/mode/agent/session/session_agent/turn/artifact), a real tmp_path
FileTransport (via OrchestratorConversation, no sbx needed), and fakes for the
sandbox provider, scorer, summarizer and synthesizer (no live sbx CLI or Anthropic
credentials needed or assumed available -- see docs/decisions.md, 2026-09-30, and
moderator/runner.py's module docstring for the compound-gap disclosure).
"""
import asyncio
import json
import uuid

import pytest
from acp import PermissionError_
from acp.floor import HandRaise, Reason
from acp.modes import ModeConfig
from acp.transport import FileTransport
from sqlalchemy import any_, select

from orchestrator.conversation import OrchestratorConversation
from orchestrator.floor import RaiseHandFloorPolicy, RoundRobinFloorPolicy, StopRulesConfig
from orchestrator.mirror import MessageMirror
from orchestrator.models import Agent, Artifact, Message as MessageRow, Mode, Session, SessionAgent, Task, Turn
from orchestrator.moderator import (
    FakeSummarizer,
    FakeSynthesizer,
    ModeratorConfig,
    ModeratorRunner,
    SummaryResult,
    SynthesisResult,
)
from orchestrator.pubsub import PubSub

from fakes import FakeSandboxProvider, fake_sandbox

CONV_ID = "c1"


async def _make_task(sessionmaker) -> uuid.UUID:
    async with sessionmaker() as session:
        row = Task(title="t", output_type="text")
        session.add(row)
        await session.commit()
        return row.id


async def _make_mode_row(sessionmaker) -> uuid.UUID:
    async with sessionmaker() as session:
        row = Mode(name="test-mode", phases_json={}, stop_rules_json={})
        session.add(row)
        await session.commit()
        return row.id


async def _make_session(sessionmaker, *, status="active", budget=None) -> uuid.UUID:
    task_id = await _make_task(sessionmaker)
    mode_id = await _make_mode_row(sessionmaker)
    async with sessionmaker() as session:
        row = Session(task_id=task_id, mode_id=mode_id, status=status, budget=budget)
        session.add(row)
        await session.commit()
        return row.id


async def _seat(sessionmaker, session_id: uuid.UUID, role: str, seat_order: int, *, muted=False) -> uuid.UUID:
    async with sessionmaker() as session:
        agent = Agent(kind="native", name=role, role=role)
        session.add(agent)
        await session.flush()
        session.add(SessionAgent(session_id=session_id, agent_id=agent.id, seat_order=seat_order, muted=muted))
        await session.commit()
        return agent.id


async def _set_muted(sessionmaker, session_id: uuid.UUID, agent_id: uuid.UUID, muted: bool) -> None:
    async with sessionmaker() as session:
        sa = await session.get(SessionAgent, (session_id, agent_id))
        sa.muted = muted
        await session.commit()


async def _set_removed(sessionmaker, session_id: uuid.UUID, agent_id: uuid.UUID) -> None:
    from datetime import datetime, timezone
    async with sessionmaker() as session:
        sa = await session.get(SessionAgent, (session_id, agent_id))
        sa.removed_at = datetime.now(timezone.utc)
        await session.commit()


def mk_mode_config(*, floor_policy="round-robin", roles=None, moderator=None) -> ModeConfig:
    roles = roles if roles is not None else {"moderator": None, "a": None, "b": None}
    return ModeConfig(name="test-mode", floor_policy=floor_policy, roles=roles, moderator=moderator)


async def _respond(transport: FileTransport, mirror: MessageMirror, participant: str, body: str,
                    *, meta=None, kind="answer") -> None:
    from acp.models import Envelope
    transport.send(Envelope(conversation_id=CONV_ID, from_=participant, to=["moderator"], kind=kind,
                             body=body, meta=meta or {}))
    await mirror.poll_once()


def mk_runner(*, tmp_path, live_sessionmaker, session_id, mode, policy, on_wake=None,
              summaries=None, synthesis=None, scorer=None, stop_rules=None, summarizer=None) -> tuple:
    conversation = OrchestratorConversation.create(conversation_id=CONV_ID, factory_dir=tmp_path, mode=mode)
    transport = FileTransport(tmp_path)
    pubsub = PubSub()
    mirror = MessageMirror(transport, live_sessionmaker, pubsub, session_id=session_id)
    sandbox_provider = FakeSandboxProvider(on_wake=on_wake)
    runner = ModeratorRunner(
        session_id=session_id, sessionmaker=live_sessionmaker, conversation=conversation,
        sandbox_provider=sandbox_provider, sandbox=fake_sandbox(), mode=mode, policy=policy,
        stop_rules=stop_rules or StopRulesConfig(), pubsub=pubsub,
        summarizer=summarizer or FakeSummarizer(
            summaries or [SummaryResult(summary="s", has_new_argument=True) for _ in range(20)]),
        synthesizer=FakeSynthesizer(synthesis or SynthesisResult(content_json={}, source_turn_ids=[])),
        scorer=scorer, config=ModeratorConfig(grant_timeout_seconds=0.3, quiet_sleep_seconds=0.05),
    )
    return runner, transport, mirror, sandbox_provider


# ---------------------------------------------------------------- opening pass

async def test_opening_pass_grants_every_seat_once_in_order(tmp_path, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    await _seat(live_sessionmaker, session_id, "b", 1)
    mode = mk_mode_config()

    async def on_wake(role):
        await _respond(transport, mirror, role, f"{role}'s opening statement")

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(), on_wake=on_wake,
    )

    await runner.run_once()  # grants "a"
    await runner.run_once()  # grants "b"

    assert sandbox.woken == ["a", "b"]
    async with live_sessionmaker() as session:
        turns = (await session.execute(select(Turn).where(Turn.session_id == session_id).order_by(Turn.seq))).scalars().all()
    assert [t.content for t in turns] == ["a's opening statement", "b's opening statement"]


async def test_opening_pass_completes_and_then_switches_to_the_policy(tmp_path, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(roles={"moderator": None, "a": None})

    async def on_wake(role):
        await _respond(transport, mirror, role, f"{role} speaks")

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(), on_wake=on_wake,
    )

    await runner.run_once()  # opening: grants "a"
    assert runner._opening_done is False  # not flipped until the *next* call finds nothing pending
    await runner.run_once()  # opening complete -> falls through to round-robin -> grants "a" again
    assert runner._opening_done is True
    assert sandbox.woken == ["a", "a"]


# ---------------------------------------------------------------- turn recording / meta

async def test_missing_meta_warns_and_continues_rather_than_failing(tmp_path, live_sessionmaker, caplog):
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(roles={"moderator": None, "a": None})

    async def on_wake(role):
        await _respond(transport, mirror, role, "no meta here")  # meta={} default

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(), on_wake=on_wake,
    )

    import logging
    with caplog.at_level(logging.WARNING):
        await runner.run_once()

    assert any("budget-accuracy degraded" in r.message for r in caplog.records)
    async with live_sessionmaker() as session:
        [turn] = (await session.execute(select(Turn).where(Turn.session_id == session_id))).scalars().all()
    assert turn.tokens_in is None and turn.tokens_out is None and turn.cost is None
    assert turn.content == "no meta here"  # the turn is still recorded -- not blocked or failed


async def test_present_meta_is_recorded_on_the_turn(tmp_path, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(roles={"moderator": None, "a": None})

    async def on_wake(role):
        await _respond(transport, mirror, role, "with meta", meta={"tokens_in": 10, "tokens_out": 20, "cost": 0.01})

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(), on_wake=on_wake,
    )
    await runner.run_once()

    async with live_sessionmaker() as session:
        [turn] = (await session.execute(select(Turn).where(Turn.session_id == session_id))).scalars().all()
    assert (turn.tokens_in, turn.tokens_out, float(turn.cost)) == (10, 20, 0.01)


# ---------------------------------------------------------------- grant timeout / escalation

async def test_grant_times_out_retries_once_then_escalates_to_human(tmp_path, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(roles={"moderator": None, "a": None})

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(), on_wake=None,  # never responds
    )

    await runner.run_once()

    assert sandbox.woken == ["a", "a"]  # initial wake + one retry wake
    async with live_sessionmaker() as session:
        row = await session.get(Session, session_id)
    assert row.status == "needs_human"
    # the reason is in the transcript, not only in the server log
    await mirror.poll_once()
    async with live_sessionmaker() as session:
        notes = (await session.execute(select(MessageRow).where(
            MessageRow.session_id == session_id, MessageRow.kind == "note"))).scalars().all()
    assert any(n.body.startswith("Paused: a did not respond") for n in notes)


# ---------------------------------------------------------------- stop conditions

async def test_max_rounds_stops_and_runs_synthesis(tmp_path, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(roles={"moderator": None, "a": None})
    # Pre-seed a Turn at round=1 so the very first stop-check already sees round >= max_rounds=1.
    async with live_sessionmaker() as session:
        agent_id = (await session.execute(select(Agent.id).where(Agent.role == "a"))).scalar_one()
        session.add(Turn(session_id=session_id, seq=1, speaker_id=agent_id, round=1, content="x"))
        await session.commit()

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(), stop_rules=StopRulesConfig(max_rounds=1),
    )
    result = await runner.run_once()

    assert result.stopped is True and result.reason == "max-rounds"
    async with live_sessionmaker() as session:
        row = await session.get(Session, session_id)
        artifacts = (await session.execute(select(Artifact).where(Artifact.session_id == session_id))).scalars().all()
    assert row.status == "finished"
    assert row.stop_reason == "max_rounds"  # M2.10: unlike budget-exceeded, still runs synthesis below
    assert [a.type for a in artifacts] == ["synthesis"]


async def test_budget_exceeded_stops_the_session(tmp_path, live_sessionmaker):
    """M2.10 product-owner-confirmed acceptance test: budget-exceeded produces zero
    additional LLM (synthesizer) calls and sets stop_reason=="budget_exceeded" with no
    synthesis artifact row -- unlike every other stop trigger (see the companion
    max-rounds test below, which still produces one)."""
    session_id = await _make_session(live_sessionmaker, budget={"max_cost": 1.0})
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(roles={"moderator": None, "a": None})
    async with live_sessionmaker() as session:
        agent_id = (await session.execute(select(Agent.id).where(Agent.role == "a"))).scalar_one()
        session.add(Turn(session_id=session_id, seq=1, speaker_id=agent_id, round=1, content="x",
                          tokens_in=60, tokens_out=50, cost=2))
        await session.commit()

    class SpySynthesizer:
        def __init__(self):
            self.calls = 0

        async def synthesize(self, view, summary, all_turns):
            self.calls += 1
            return SynthesisResult(content_json={}, source_turn_ids=[])

    spy = SpySynthesizer()
    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(),
    )
    runner._synthesizer = spy  # swap in the spy after construction, same object otherwise
    result = await runner.run_once()

    assert result.stopped is True and result.reason == "budget-exceeded"
    assert spy.calls == 0  # zero additional LLM calls after the cap trips
    async with live_sessionmaker() as session:
        row = await session.get(Session, session_id)
        artifacts = (await session.execute(select(Artifact).where(Artifact.session_id == session_id))).scalars().all()
    assert row.status == "finished"
    assert row.stop_reason == "budget_exceeded"
    assert artifacts == []  # no synthesis artifact row -- contrast with test_max_rounds_stops_and_runs_synthesis
    # above, the "still produces one" half of the same product-owner acceptance test.


async def test_budget_stops_before_a_turn_that_would_cross_the_cap(tmp_path, live_sessionmaker):
    """Under the cap, but the next turn (at the average cost so far) would cross it: stop now, don't overshoot."""
    session_id = await _make_session(live_sessionmaker, budget={"max_cost": 1.0})
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(roles={"moderator": None, "a": None})
    async with live_sessionmaker() as session:
        agent_id = (await session.execute(select(Agent.id).where(Agent.role == "a"))).scalar_one()
        session.add(Turn(session_id=session_id, seq=1, speaker_id=agent_id, round=1, content="x",
                          tokens_in=60, tokens_out=50, cost=0.6))
        await session.commit()
    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(),
    )
    result = await runner.run_once()
    assert result.stopped is True and result.reason == "budget-exceeded"  # 0.6 spent + ~0.6 next > 1.0


async def test_token_budget_is_a_suggestion_not_a_limit(tmp_path, live_sessionmaker):
    """Product-owner decision 2026-10-09: exceeding max_tokens must NOT stop the session
    (only max_cost does)."""
    session_id = await _make_session(live_sessionmaker, budget={"max_tokens": 100})
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(roles={"moderator": None, "a": None})
    async with live_sessionmaker() as session:
        agent_id = (await session.execute(select(Agent.id).where(Agent.role == "a"))).scalar_one()
        session.add(Turn(session_id=session_id, seq=1, speaker_id=agent_id, round=1, content="x",
                          tokens_in=600, tokens_out=500))
        await session.commit()
    runner, *_ = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(),
    )
    async with live_sessionmaker() as session:
        row = await session.get(Session, session_id)
    assert await runner._budget_exceeded(row) is False


async def test_no_new_arguments_for_k_turns_stops_the_session(tmp_path, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(roles={"moderator": None, "a": None})
    async with live_sessionmaker() as session:
        row = await session.get(Session, session_id)
        row.stale_argument_count = 3  # already at the default stale_argument_turns=3 threshold
        await session.commit()

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(),
    )
    result = await runner.run_once()

    assert result.stopped is True and result.reason == "no-new-arguments"


async def test_external_status_change_halts_the_loop_and_still_runs_synthesis(tmp_path, live_sessionmaker):
    """"user-ends-it": genuinely external and different from needs_human (a pause) -- e.g.
    M2.8's not-yet-built "stop" verb setting some other terminal value. Unlike a pause,
    this DOES wrap up with synthesis (same as the other stop triggers)."""
    session_id = await _make_session(live_sessionmaker, status="cancelled")  # not "active", not "needs_human"
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(roles={"moderator": None, "a": None})

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(),
    )
    result = await runner.run_once()

    assert result.stopped is True and result.reason == "external-status-change"
    assert sandbox.woken == []  # never got to dispatching anything
    async with live_sessionmaker() as session:
        artifacts = (await session.execute(select(Artifact).where(Artifact.session_id == session_id))).scalars().all()
        row = await session.get(Session, session_id)
    assert [a.type for a in artifacts] == ["synthesis"]
    assert row.status == "finished"


async def test_needs_human_pause_stops_without_running_synthesis(tmp_path, live_sessionmaker):
    """A pause (whether from an earlier AskHuman escalation or a human directly) is not a
    wrap-up: the loop stops, but no synthesis runs and no Artifact is created."""
    session_id = await _make_session(live_sessionmaker, status="needs_human")
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(roles={"moderator": None, "a": None})

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(),
    )
    result = await runner.run_once()

    assert result.stopped is True and result.reason == "needs_human"
    assert sandbox.woken == []
    async with live_sessionmaker() as session:
        artifacts = (await session.execute(select(Artifact).where(Artifact.session_id == session_id))).scalars().all()
        row = await session.get(Session, session_id)
    assert artifacts == []
    assert row.status == "needs_human"  # unchanged -- run_once() didn't touch it further


async def test_convergence_from_the_floor_policy_triggers_synthesis(tmp_path, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    await _seat(live_sessionmaker, session_id, "b", 1)
    mode = mk_mode_config(floor_policy="raise-hand", roles={"moderator": None, "a": None, "b": None})
    # Opening pass needs both to speak once before raise-hand kicks in.
    responses = iter(["a's opening", "b's opening"])

    async def on_wake(role):
        await _respond(transport, mirror, role, next(responses))

    class NoHandsScorer:
        async def score(self, view, personas):
            return []

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RaiseHandFloorPolicy(StopRulesConfig(converge_after_quiet_rounds=1)),
        on_wake=on_wake, scorer=NoHandsScorer(),
    )

    await runner.run_once()  # opening: a
    await runner.run_once()  # opening: b
    result = await runner.run_once()  # raise-hand, no hands raised -> converges (K=1)

    assert result.stopped is True and result.reason == "converged"
    async with live_sessionmaker() as session:
        row = await session.get(Session, session_id)
    assert row.status == "finished"


# ---------------------------------------------------------------- minority report

async def test_no_dissent_produces_only_the_synthesis_artifact(tmp_path, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker, status="cancelled")  # force an immediate stop+synthesis
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(roles={"moderator": None, "a": None})

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(),
    )
    # Force through _finish directly via the external-status-change path (see
    # test_external_status_change_halts_the_loop_and_still_runs_synthesis).
    await runner.run_once()

    async with live_sessionmaker() as session:
        artifacts = (await session.execute(select(Artifact).where(Artifact.session_id == session_id))).scalars().all()
    assert [a.type for a in artifacts] == ["synthesis"]


async def test_a_clear_dissent_produces_exactly_one_consolidated_minority_report(tmp_path, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker, status="cancelled")
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(roles={"moderator": None, "a": None})

    dissent = {"objection": "the holdout's specific stated objection", "alternate": None}
    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(),
        synthesis=SynthesisResult(content_json={"summary": "consensus reached"}, source_turn_ids=[],
                                   minority_report=dissent),
    )
    await runner.run_once()

    async with live_sessionmaker() as session:
        artifacts = (await session.execute(select(Artifact).where(Artifact.session_id == session_id))).scalars().all()
    by_type = {a.type: a for a in artifacts}
    assert set(by_type) == {"synthesis", "minority_report"}
    assert by_type["minority_report"].content_json == dissent


# ---------------------------------------------------------------- self-reported hand-raises

async def test_self_reported_hand_raise_message_is_parsed_and_merged(tmp_path, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    await _seat(live_sessionmaker, session_id, "b", 1)
    mode = mk_mode_config(floor_policy="raise-hand", roles={"moderator": None, "a": None, "b": None})

    responses = iter(["a's opening", "b's opening"])

    async def on_wake(role):
        await _respond(transport, mirror, role, next(responses))

    hand_raise = HandRaise(participant="b", reason=Reason.NEW_POINT, urgency=0.9)

    class NoHandsScorer:
        async def score(self, view, personas):
            return []

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RaiseHandFloorPolicy(), on_wake=on_wake, scorer=NoHandsScorer(),
    )

    await runner.run_once()  # opening: a
    await runner.run_once()  # opening: b

    # "b" self-emits a hand-raise: an ordinary Envelope, kind="hand-raise", body = the
    # HandRaise model's own JSON (the wire convention this task documents/invents).
    from acp.models import Envelope
    transport.send(Envelope(conversation_id=CONV_ID, from_="b", to=["moderator"], kind="hand-raise",
                             body=json.dumps(hand_raise.model_dump(mode="json"))))
    await mirror.poll_once()

    raised = await runner._collect_hand_raises(await runner._build_view())
    assert raised == [hand_raise]


# ---------------------------------------------------------------- M2.10 OTel spans

async def test_run_once_creates_the_expected_span_hierarchy(tmp_path, live_sessionmaker, span_exporter):
    """The nested hierarchy from the architect's design: moderator.run_once is the parent
    of moderator.grant_wait, which is the parent of turn.process; moderator.summarize (a
    real AnthropicSummarizer here, since fakes create no spans) is a SIBLING of
    grant_wait, not nested inside it -- it fires after the grant_wait span already closed.
    turn.process's bayto.turn_id is cross-checked against the actual inserted Turn row.
    Needs live Postgres for that correlation; span mechanics are otherwise DB-independent.
    """
    from types import SimpleNamespace

    from orchestrator.moderator.summarizer import AnthropicSummarizer

    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(roles={"moderator": None, "a": None})

    async def on_wake(role):
        await _respond(transport, mirror, role, "hi", meta={"tokens_in": 5, "tokens_out": 7, "cost": 0.001})

    class FakeMessages:
        async def create(self, **kwargs):
            block = SimpleNamespace(type="tool_use", name="submit_summary",
                                     input={"summary": "s", "has_new_argument": True})
            response = SimpleNamespace(content=[block])
            response.usage = SimpleNamespace(input_tokens=1, output_tokens=1)
            return response

    real_summarizer = AnthropicSummarizer(model="claude-haiku-fake", client=SimpleNamespace(messages=FakeMessages()))

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(), on_wake=on_wake, summarizer=real_summarizer,
    )
    await runner.run_once()

    spans = span_exporter.get_finished_spans()
    by_name = {s.name: s for s in spans}
    assert set(by_name) == {"moderator.run_once", "moderator.grant_wait", "turn.process", "moderator.summarize"}
    for s in spans:
        assert s.end_time >= s.start_time

    run_once, grant_wait, turn_process, summarize = (
        by_name["moderator.run_once"], by_name["moderator.grant_wait"],
        by_name["turn.process"], by_name["moderator.summarize"],
    )
    assert grant_wait.parent.span_id == run_once.context.span_id
    assert turn_process.parent.span_id == grant_wait.context.span_id
    # Sibling of grant_wait under run_once -- NOT nested inside it.
    assert summarize.parent.span_id == run_once.context.span_id

    assert run_once.attributes["bayto.session_id"] == str(session_id)
    assert run_once.attributes["bayto.round"] == 0
    assert grant_wait.attributes["bayto.session_id"] == str(session_id)
    assert grant_wait.attributes["bayto.participant"] == "a"
    assert grant_wait.attributes["outcome"] == "responded"
    assert summarize.attributes["bayto.session_id"] == str(session_id)
    assert summarize.attributes["gen_ai.usage.input_tokens"] == 1

    async with live_sessionmaker() as session:
        turn = (await session.execute(select(Turn).where(Turn.session_id == session_id))).scalar_one()
    assert turn_process.attributes["bayto.session_id"] == str(session_id)
    assert turn_process.attributes["bayto.turn_id"] == str(turn.id)
    assert turn_process.attributes["bayto.participant"] == "a"
    assert turn_process.attributes["gen_ai.usage.input_tokens"] == 5
    assert turn_process.attributes["gen_ai.usage.output_tokens"] == 7
    assert turn_process.attributes["bayto.cost_usd"] == 0.001


async def test_missing_meta_produces_a_turn_process_span_with_no_usage_attributes(
        tmp_path, live_sessionmaker, span_exporter):
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(roles={"moderator": None, "a": None})

    async def on_wake(role):
        await _respond(transport, mirror, role, "no meta here")

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(), on_wake=on_wake,
    )
    await runner.run_once()

    [turn_process] = [s for s in span_exporter.get_finished_spans() if s.name == "turn.process"]
    assert "gen_ai.usage.input_tokens" not in turn_process.attributes
    assert "gen_ai.usage.output_tokens" not in turn_process.attributes
    assert "bayto.cost_usd" not in turn_process.attributes


async def test_grant_timeout_sets_outcome_timeout_on_the_grant_wait_span(tmp_path, live_sessionmaker, span_exporter):
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(roles={"moderator": None, "a": None})

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(),  # never responds
    )
    await runner.run_once()

    [grant_wait] = [s for s in span_exporter.get_finished_spans() if s.name == "moderator.grant_wait"]
    assert grant_wait.attributes["outcome"] == "timeout"


# ---------------------------------------------------------------- M3.2: participant lifecycle

async def test_muting_the_next_due_participant_mid_rotation_skips_them(tmp_path, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker)
    a_id = await _seat(live_sessionmaker, session_id, "a", 0)
    await _seat(live_sessionmaker, session_id, "b", 1)
    mode = mk_mode_config()

    async def on_wake(role):
        await _respond(transport, mirror, role, f"{role} speaks")

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(), on_wake=on_wake,
    )

    await runner.run_once()  # opening: a
    await runner.run_once()  # opening: b -- last_speaker is now "b", "a" is next due

    await _set_muted(live_sessionmaker, session_id, a_id, True)
    await runner.run_once()  # would normally grant "a" again -- "a" is muted, so "b" instead

    assert sandbox.woken == ["a", "b", "b"]


async def test_removing_a_participant_mid_rotation_skips_them_like_mute(tmp_path, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker)
    a_id = await _seat(live_sessionmaker, session_id, "a", 0)
    await _seat(live_sessionmaker, session_id, "b", 1)
    mode = mk_mode_config()

    async def on_wake(role):
        await _respond(transport, mirror, role, f"{role} speaks")

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(), on_wake=on_wake,
    )

    await runner.run_once()  # opening: a
    await runner.run_once()  # opening: b

    await _set_removed(live_sessionmaker, session_id, a_id)
    await runner.run_once()  # "a" removed -- "b" gets it instead

    assert sandbox.woken == ["a", "b", "b"]

    # Prior turns from the removed participant stay fully visible -- Turn rows are
    # immutable/append-only throughout this system; removal never touches them.
    async with live_sessionmaker() as session:
        turns = (await session.execute(
            select(Turn).where(Turn.session_id == session_id).order_by(Turn.seq)
        )).scalars().all()
    assert [t.content for t in turns][:2] == ["a speaks", "b speaks"]
    assert turns[0].speaker_id == a_id


async def test_adding_a_participant_mid_session_round_robin_eventually_gets_a_turn(tmp_path, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    await _seat(live_sessionmaker, session_id, "b", 1)
    # A wildcard roster (M2.11's "*", used by the actual target modes -- open-chat/debate/
    # brainstorm) so the newly-added "c" is a permitted Envelope recipient. A FIXED-roles
    # mode would reject sending to "c" at the acp.core.Conversation/Roster layer unless "c"
    # was already declared in mode.roles -- a separate, protocol-level roster from
    # SessionAgent's floor-control-only one; see docs/decisions.md.
    mode = mk_mode_config(roles={"*": None})

    async def on_wake(role):
        await _respond(transport, mirror, role, f"{role} speaks")

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(), on_wake=on_wake,
    )

    await runner.run_once()  # opening: a
    await runner.run_once()  # opening: b -- self._opening_done is still False here (only
                              # flips once a call finds NOBODY pending), so a participant
                              # added right now is swept into the still-open opening pass.
    await _seat(live_sessionmaker, session_id, "c", 2)  # added mid-session, appended to the rotation

    await runner.run_once()  # opening: "c" -- the new participant's first turn, immediately
    await runner.run_once()  # opening complete (nobody left pending) -> round-robin -> "a"
    await runner.run_once()  # round-robin: "b"
    await runner.run_once()  # round-robin: "c" again -- full rotation now genuinely includes them

    assert sandbox.woken == ["a", "b", "c", "a", "b", "c"]


async def test_adding_a_participant_whose_role_is_undeclared_in_a_fixed_roster_mode_fails_to_send(
        tmp_path, live_sessionmaker):
    """Documents a real, disclosed-but-not-fixed limitation (see docs/decisions.md): mid-
    session add only works when the new participant's role is already a permitted
    recipient at the acp.core.Conversation/Roster layer. Unlike M2.11's wildcard-roster
    discussion modes (the only ones this orchestrator actually ships), a FIXED-roster mode
    never declared "c" in its `roles` dict, so the Roster built once at session start has
    no entry for them -- the moderator's attempt to address them during the opening pass
    fails outright, even though SessionAgent/floor-control has no problem with the new
    seat. Not a bug this task fixes; a future mode needing this would need a change in
    acp.modes/acp.models.Roster, not here."""
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    await _seat(live_sessionmaker, session_id, "b", 1)
    mode = mk_mode_config(roles={"moderator": None, "a": None, "b": None})  # fixed roster -- no "c"

    async def on_wake(role):
        await _respond(transport, mirror, role, f"{role} speaks")

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(), on_wake=on_wake,
    )

    await runner.run_once()  # opening: a
    await runner.run_once()  # opening: b

    await _seat(live_sessionmaker, session_id, "c", 2)  # "c" was never declared in mode.roles

    with pytest.raises(PermissionError_):
        await runner.run_once()  # the opening pass tries to address "c" -> rejected by the Roster


async def test_adding_a_participant_mid_session_raise_hand_gets_one_opening_grant_first(tmp_path, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(floor_policy="raise-hand", roles={"moderator": None, "a": None, "b": None})

    async def on_wake(role):
        await _respond(transport, mirror, role, f"{role} speaks")

    class NoHandsScorer:
        async def score(self, view, personas):
            return []

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RaiseHandFloorPolicy(), on_wake=on_wake, scorer=NoHandsScorer(),
    )

    await runner.run_once()  # opening: a
    result = await runner.run_once()  # nobody else pending -> opening_done flips True in this same
    assert runner._opening_done is True                        # call, falls through to raise-hand (no hands -> quiet)
    assert result.quiet is True

    await _seat(live_sessionmaker, session_id, "b", 1)  # added mid-session, after opening_done

    class FailIfCalledScorer:
        async def score(self, view, personas):
            raise AssertionError("scorer should not run while a late-joiner's opening grant is pending")

    runner._scorer = FailIfCalledScorer()  # proves the next grant is NOT scored competition
    await runner.run_once()  # "b" gets a late-joiner opening-style grant instead

    assert sandbox.woken == ["a", "b"]
    async with live_sessionmaker() as session:
        turns = (await session.execute(select(Turn).where(Turn.session_id == session_id).order_by(Turn.seq))).scalars().all()
    assert [t.content for t in turns] == ["a speaks", "b speaks"]

    # The actual claim that makes "add" functional for raise-hand mode, not just a nice-to-
    # have: once the late-joiner's own opening grant is done, the NEXT round is genuinely
    # scored, and the newcomer is offered to the scorer like anyone else -- not just granted
    # once via the opening path and then forgotten.
    captured: list = []

    class CapturingScorer:
        async def score(self, view, personas):
            captured.append(personas)
            return []

    runner._scorer = CapturingScorer()
    await runner.run_once()  # nobody left pending -> genuine scored raise-hand round

    assert len(captured) == 1
    assert {p.participant for p in captured[0]} == {"a", "b"}


async def test_muted_participant_never_speaks_and_is_excluded_from_the_scorer(tmp_path, live_sessionmaker):
    """Muted from the very start of the session: mute is an absolute eligibility gate
    (M3.2), so they never get an opening turn either -- not just excluded from scored
    raise-hand competition afterward."""
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0, muted=True)
    await _seat(live_sessionmaker, session_id, "b", 1)
    mode = mk_mode_config(floor_policy="raise-hand", roles={"moderator": None, "a": None, "b": None})

    async def on_wake(role):
        await _respond(transport, mirror, role, f"{role}'s opening")

    captured: list = []

    class CapturingScorer:
        async def score(self, view, personas):
            captured.append(personas)
            return []

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RaiseHandFloorPolicy(StopRulesConfig(converge_after_quiet_rounds=1)),
        on_wake=on_wake, scorer=CapturingScorer(),
    )

    await runner.run_once()  # opening skips muted "a" entirely -> grants "b"
    await runner.run_once()  # nobody else pending -> opening complete -> raise-hand -> scorer called

    assert sandbox.woken == ["b"]  # "a" never spoke at all
    assert len(captured) == 1
    assert {p.participant for p in captured[0]} == {"b"}


# ---------------------------------------------------------------- rolling summary broadcast (M3.4)

async def test_rolling_summary_publishes_to_pubsub(tmp_path, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config()

    async def on_wake(role):
        await _respond(transport, mirror, role, "A response to trigger summary")

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(), on_wake=on_wake,
        summaries=[SummaryResult(summary="The new summary text", has_new_argument=True)]
    )

    queue = runner._pubsub.subscribe()
    await runner.run_once()

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    summary_events = [e for e in events if e.get("type") == "rolling_summary"]
    assert len(summary_events) == 1
    assert summary_events[0]["data"]["summary"] == "The new summary text"


# ---------------------------------------------------------------- streaming turns (M5)

async def test_streaming_turn_publishes_deltas_and_records_turn(tmp_path, live_sessionmaker):
    from orchestrator.sandbox.turns import TextDelta, TurnResult
    from fakes import FakeStreamingProvider

    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config()
    runner, transport, mirror, _ = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy())
    provider = FakeStreamingProvider([TextDelta("hel"), TextDelta("lo"),
                                      TurnResult(text="hello", tokens_in=10, tokens_out=5, cost=0.01, session_id="s")])
    runner._sandbox_provider = provider
    seen = runner._pubsub.subscribe()

    async def mirror_later():
        for _ in range(50):
            await asyncio.sleep(0.02)
            await mirror.poll_once()
    task = asyncio.create_task(mirror_later())
    await runner.run_once()
    task.cancel()

    types = []
    while not seen.empty():
        types.append(seen.get_nowait()["type"])
    assert types.count("turn_delta") == 2 and "turn_done" in types
    assert provider.woken == []  # the seat was not poked: the turn ran headless
    async with live_sessionmaker() as session:
        turn = (await session.execute(select(Turn).where(Turn.session_id == session_id))).scalars().one()
    assert turn.content == "hello" and turn.tokens_in == 10 and float(turn.cost) == 0.01


# ---------------------------------------------------------------- the task reaches the seats and the synthesis

async def _set_task(sessionmaker, session_id, **fields):
    from orchestrator.models import Task
    async with sessionmaker() as session:
        row = await session.get(Session, session_id)
        task = await session.get(Task, row.task_id)
        for k, v in fields.items():
            setattr(task, k, v)
        await session.commit()


async def test_a_seats_prompt_contains_the_task(tmp_path, live_sessionmaker):
    """Seats are fresh runs that know nothing but the prompt. The first live run woke them with only the
    rolling summary, so they answered "nothing in my inbox" and the session ended on `staleness`."""
    from orchestrator.sandbox.turns import TurnResult
    from fakes import FakeStreamingProvider

    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    await _set_task(live_sessionmaker, session_id, title="Weekly or biweekly releases?",
                    brief="Decide the release cadence for next quarter.", output_type="decision",
                    success_criteria="Names one cadence and the main risk.")
    runner, transport, mirror, _ = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mk_mode_config(), policy=RoundRobinFloorPolicy())
    provider = FakeStreamingProvider([TurnResult(text="hi", tokens_in=1, tokens_out=1, cost=0, session_id="s")])
    runner._sandbox_provider = provider

    async def mirror_later():
        for _ in range(50):
            await asyncio.sleep(0.02)
            await mirror.poll_once()
    task = asyncio.create_task(mirror_later())
    await runner.run_once()
    task.cancel()

    prompt = provider.prompts[0][1]
    assert "Task: Weekly or biweekly releases?" in prompt
    assert "Brief: Decide the release cadence for next quarter." in prompt
    assert "Expected output: decision" in prompt
    assert "Success criteria: Names one cadence and the main risk." in prompt


async def test_task_text_omits_the_optional_parts_it_does_not_have(tmp_path, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker)
    await _set_task(live_sessionmaker, session_id, title="T", brief=None, output_type="memo", success_criteria=None)
    runner, *_ = mk_runner(tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
                           mode=mk_mode_config(), policy=RoundRobinFloorPolicy())
    assert await runner._task_text() == "Task: T\nExpected output: memo"


async def test_the_synthesis_prompt_includes_the_task(tmp_path, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker)
    await _set_task(live_sessionmaker, session_id, title="Pick a cadence", output_type="decision")

    class SpySynthesizer:
        hints = []

        async def synthesize(self, view, summary, all_turns, synthesis_prompt_hint=None):
            self.hints.append(synthesis_prompt_hint)
            return SynthesisResult(content_json={}, source_turn_ids=[])

    runner, *_ = mk_runner(tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
                           mode=mk_mode_config(), policy=RoundRobinFloorPolicy())
    runner._synthesizer = SpySynthesizer()
    await runner._finish(await runner._build_view(), "max-rounds")
    assert "Task: Pick a cadence" in SpySynthesizer.hints[0] and "Expected output: decision" in SpySynthesizer.hints[0]


# ---------------------------------------------------------------- Build mode (pipeline floor policy)

BUILD_ROUTE = {
    "start": "architect",
    "architect.report.planned": "developer",
    "developer.report.implemented": "qa-tester",
    "qa-tester.report.pass": "reviewer",
    "qa-tester.report.fail": "developer",
    "reviewer.report.approve": "done",
}


async def _build_session(live_sessionmaker, tmp_path, script, *, max_fix_cycles=3):
    """Seats the four Build roles in a deliberately non-pipeline order and plays `script` (role -> list of
    reply bodies, consumed in order) through the real runner, mirror and database."""
    from orchestrator.floor import PipelineFloorPolicy

    session_id = await _make_session(live_sessionmaker)
    for i, role in enumerate(["reviewer", "qa-tester", "developer", "architect"]):
        await _seat(live_sessionmaker, session_id, role, i)
    mode = ModeConfig(name="build", floor_policy="pipeline", roles={"moderator": None, "*": None},
                      route=BUILD_ROUTE, moderator=None)
    replies = {role: list(bodies) for role, bodies in script.items()}

    async def on_wake(role):
        await _respond(transport, mirror, role, replies[role].pop(0))

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id, mode=mode,
        policy=PipelineFloorPolicy(BUILD_ROUTE, max_fix_cycles=max_fix_cycles), on_wake=on_wake,
        stop_rules=StopRulesConfig(max_fix_cycles=max_fix_cycles),
    )
    return session_id, runner, sandbox


async def _run_to_stop(runner, limit=20):
    for _ in range(limit):
        result = await runner.run_once()
        if result.stopped:
            return result
    raise AssertionError("runner never stopped")


async def test_build_pipeline_plans_builds_fails_fixes_passes_and_finishes(tmp_path, live_sessionmaker):
    session_id, runner, sandbox = await _build_session(live_sessionmaker, tmp_path, {
        "architect": ["Plan: touch a.py.\nVERDICT: planned"],
        "developer": ["Wrote a.py.\nVERDICT: implemented", "Fixed the off-by-one.\nVERDICT: implemented"],
        "qa-tester": ["pytest exit 1\nVERDICT: fail", "pytest exit 0\nVERDICT: pass"],
        "reviewer": ["Diff reads well.\nVERDICT: approve"],
    })

    result = await _run_to_stop(runner)

    # No opening lap: the architect starts even though the reviewer is seat 0, and each role speaks as routed.
    assert sandbox.woken == ["architect", "developer", "qa-tester", "developer", "qa-tester", "reviewer"]
    assert result.reason == "build-complete"
    async with live_sessionmaker() as db:
        row = await db.get(Session, session_id)
        assert row.status == "finished" and row.stop_reason == "build_complete"


async def test_build_pipeline_pauses_for_a_human_when_fix_cycles_run_out(tmp_path, live_sessionmaker):
    session_id, runner, sandbox = await _build_session(live_sessionmaker, tmp_path, {
        "architect": ["Plan.\nVERDICT: planned"],
        "developer": ["try 1\nVERDICT: implemented", "try 2\nVERDICT: implemented"],
        "qa-tester": ["red\nVERDICT: fail", "red\nVERDICT: fail"],
    }, max_fix_cycles=1)

    result = await _run_to_stop(runner)

    assert result.reason == "needs_human"
    assert sandbox.woken == ["architect", "developer", "qa-tester", "developer", "qa-tester"]
    async with live_sessionmaker() as db:
        row = await db.get(Session, session_id)
        assert row.status == "needs_human"  # paused with the work kept, not failed


async def test_build_pipeline_ignores_staleness(tmp_path, live_sessionmaker):
    """Staleness is a discussion measure; the summarizer saying 'no new argument' must not stop a build."""
    from orchestrator.floor import PipelineFloorPolicy  # noqa: F401  (imported in _build_session)

    session_id, runner, sandbox = await _build_session(live_sessionmaker, tmp_path, {
        "architect": ["Plan.\nVERDICT: planned"],
        "developer": ["ok\nVERDICT: implemented"],
        "qa-tester": ["ok\nVERDICT: pass"],
        "reviewer": ["ok\nVERDICT: approve"],
    })
    runner._summarizer = FakeSummarizer([SummaryResult(summary="s", has_new_argument=False) for _ in range(20)])

    result = await _run_to_stop(runner)

    assert result.reason == "build-complete"


async def test_build_turn_prompt_carries_the_task_workspace_and_the_verdict_contract(tmp_path, live_sessionmaker):
    session_id, runner, _ = await _build_session(live_sessionmaker, tmp_path, {})
    async with live_sessionmaker() as db:
        task = await db.get(Task, (await db.get(Session, session_id)).task_id)
        task.workspace = {"repo_path": "/r", "base_branch": "develop", "test_command": "pytest -q"}
        await db.commit()

    prompt = await runner._build_context(await runner._build_view())

    assert "build pipeline" in prompt and "discussion" not in prompt
    assert "base branch develop" in prompt and "Test command: pytest -q" in prompt
    assert "VERDICT: <word>" in prompt


async def test_build_pipeline_sends_a_bare_pass_back_and_tells_the_tester_why(tmp_path, live_sessionmaker):
    from orchestrator.floor import PipelineFloorPolicy

    session_id = await _make_session(live_sessionmaker)
    for i, role in enumerate(["reviewer", "qa-tester", "developer", "architect"]):
        await _seat(live_sessionmaker, session_id, role, i)
    mode = ModeConfig(name="build", floor_policy="pipeline", roles={"moderator": None, "*": None},
                      route=BUILD_ROUTE, moderator=None)
    replies = {
        "architect": ["Plan.\nVERDICT: planned"],
        "developer": ["ok\nVERDICT: implemented"],
        "qa-tester": ["Looks good.\nVERDICT: pass", "Ran `pytest -q`\nexit code: 0\n3 passed\nVERDICT: pass"],
        "reviewer": ["ok\nVERDICT: approve"],
    }

    async def on_wake(role):
        await _respond(transport, mirror, role, replies[role].pop(0))

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id, mode=mode,
        policy=PipelineFloorPolicy(BUILD_ROUTE, require_evidence={"qa-tester.report.pass"}), on_wake=on_wake)

    prompts = []
    original = runner._build_context

    async def spy(view):
        text = await original(view)
        prompts.append(text)
        return text
    runner._build_context = spy

    result = await _run_to_stop(runner)

    assert result.reason == "build-complete"
    assert sandbox.woken == ["architect", "developer", "qa-tester", "qa-tester", "reviewer"]
    retry_prompt = prompts[3]  # the tester's second turn
    assert "without showing its work" in retry_prompt and "exit code" in retry_prompt
    assert "NOTE:" not in prompts[4]  # the note does not leak into the reviewer's turn


async def test_finished_build_session_gets_a_change_set_read_from_the_repo(tmp_path, live_sessionmaker):
    import subprocess

    from orchestrator.workspace import build_branch_name

    repo = tmp_path / "proj"
    repo.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
    g("init", "-q", "-b", "main"); g("config", "user.email", "t@e.com"); g("config", "user.name", "t")
    (repo / "a.txt").write_text("a"); g("add", "."); g("commit", "-q", "-m", "init")

    session_id, runner, sandbox = await _build_session(live_sessionmaker, tmp_path / "f", {
        "architect": ["Plan.\nVERDICT: planned"], "developer": ["ok\nVERDICT: implemented"],
        "qa-tester": ["`pytest -q`\nexit code: 0\n1 passed\nVERDICT: pass"], "reviewer": ["ok\nVERDICT: approve"],
    }) if (tmp_path / "f").mkdir() is None else (None, None, None)
    # the "developer" left a commit on the session's branch
    g("switch", "-q", "-c", build_branch_name(session_id)); (repo / "new.py").write_text("1\n")
    g("add", "."); g("commit", "-q", "-m", "feat: new"); g("switch", "-q", "main")
    async with live_sessionmaker() as db:
        task = await db.get(Task, (await db.get(Session, session_id)).task_id)
        task.workspace = {"repo_path": str(repo), "base_branch": "main", "test_command": "pytest -q"}
        await db.commit()

    await _run_to_stop(runner)

    async with live_sessionmaker() as db:
        arts = (await db.execute(select(Artifact).where(Artifact.session_id == session_id))).scalars().all()
    by_type = {a.type: a.content_json for a in arts}
    assert set(by_type) == {"synthesis", "change_set"}
    cs = by_type["change_set"]
    assert cs["status"] == "ready_to_merge" and cs["commits"][0]["subject"] == "feat: new"
    assert cs["tests"]["exit_code"] == 0 and cs["review"]["verdict"] == "approve"


async def test_runner_undoes_tampering_with_git_settings_before_describing_the_result(tmp_path, live_sessionmaker):
    import subprocess

    from orchestrator.git_guard import save_baseline
    from orchestrator.workspace import build_branch_name

    repo = tmp_path / "proj"
    repo.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
    g("init", "-q", "-b", "main"); g("config", "user.email", "t@e.com"); g("config", "user.name", "t")
    (repo / "a.txt").write_text("a"); g("add", "."); g("commit", "-q", "-m", "init")
    guard = tmp_path / "guard.json"
    save_baseline(str(repo), guard)

    (tmp_path / "f").mkdir()
    session_id, runner, _ = await _build_session(live_sessionmaker, tmp_path / "f", {
        "architect": ["Plan.\nVERDICT: planned"], "developer": ["ok\nVERDICT: implemented"],
        "qa-tester": ["`pytest -q`\nexit code: 0\n1 passed\nVERDICT: pass"], "reviewer": ["ok\nVERDICT: approve"],
    })
    runner._git_guard_file = guard
    g("switch", "-q", "-c", build_branch_name(session_id)); (repo / "new.py").write_text("1\n")
    g("add", "."); g("commit", "-q", "-m", "feat: new"); g("switch", "-q", "main")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nevil\n")  # what a seat could leave behind
    async with live_sessionmaker() as db:
        task = await db.get(Task, (await db.get(Session, session_id)).task_id)
        task.workspace = {"repo_path": str(repo), "base_branch": "main", "test_command": "pytest -q"}
        await db.commit()

    await _run_to_stop(runner)

    assert not hook.exists()
    async with live_sessionmaker() as db:
        arts = (await db.execute(select(Artifact).where(Artifact.session_id == session_id))).scalars().all()
    cs = {a.type: a.content_json for a in arts}["change_set"]
    assert cs["status"] == "needs_you" and cs["git_guard"]["changed"] == ["git hook 'pre-commit' was added"]


async def test_build_turn_prompt_includes_an_earlier_sessions_synthesis(tmp_path, live_sessionmaker):
    session_id, runner, _ = await _build_session(live_sessionmaker, tmp_path, {})
    async with live_sessionmaker() as db:
        task_id = (await db.get(Session, session_id)).task_id
        mode_id = (await db.get(Session, session_id)).mode_id
        old = Session(task_id=task_id, mode_id=mode_id, status="finished")
        db.add(old)
        await db.flush()
        db.add(Artifact(session_id=old.id, type="synthesis", content_json={"decision": "use a queue, not cron"},
                        source_turn_ids=[]))
        await db.commit()

    prompt = await runner._build_context(await runner._build_view())

    assert "Earlier synthesis" in prompt and "use a queue, not cron" in prompt


# ---- A human message sent mid-session reaches the agents' prompts

async def _human_says(runner, mirror, body, to=("*",)):
    await runner._conversation.send(sender="human", to=list(to), kind="note", body=body)
    await mirror.poll_once()


async def _grant_prompt(live_sessionmaker, runner, role):
    """Grants the floor to `role` and returns the prompt it was given (the assignment body)."""
    from orchestrator.floor.types import Grant
    from orchestrator.models import Message
    await runner._handle_grant(await runner._build_view(), Grant(participant=role, reason="test"))
    async with live_sessionmaker() as db:
        rows = (await db.execute(select(Message).where(Message.kind == "assignment", role == any_(Message.to))
                                 .order_by(Message.seq.desc()))).scalars().all()
    return rows[0].body


async def test_a_human_note_reaches_the_next_prompt_once_per_agent(tmp_path, live_sessionmaker):
    reply = "did it\nVERDICT: planned"
    session_id, runner, _ = await _build_session(live_sessionmaker, tmp_path, {
        "architect": [reply] * 3, "developer": ["ok\nVERDICT: implemented"]})
    from orchestrator.mirror import MessageMirror
    from orchestrator.pubsub import PubSub
    mirror = MessageMirror(FileTransport(tmp_path), live_sessionmaker, PubSub(), session_id=session_id)

    first = await _grant_prompt(live_sessionmaker, runner, "architect")
    assert "MESSAGE FROM THE HUMAN" not in first

    await _human_says(runner, mirror, "Use sqlite, not postgres.")
    second = await _grant_prompt(live_sessionmaker, runner, "architect")
    assert "MESSAGE FROM THE HUMAN" in second and "Use sqlite, not postgres." in second

    third = await _grant_prompt(live_sessionmaker, runner, "architect")
    assert "Use sqlite" not in third  # already delivered, not repeated

    other = await _grant_prompt(live_sessionmaker, runner, "developer")
    assert "Use sqlite, not postgres." in other  # a different agent has not seen it yet


async def test_a_directed_note_goes_only_to_the_addressee(tmp_path, live_sessionmaker):
    session_id, runner, _ = await _build_session(live_sessionmaker, tmp_path, {
        "architect": ["a\nVERDICT: planned"], "developer": ["d\nVERDICT: implemented"]})
    from orchestrator.mirror import MessageMirror
    from orchestrator.pubsub import PubSub
    mirror = MessageMirror(FileTransport(tmp_path), live_sessionmaker, PubSub(), session_id=session_id)
    await _human_says(runner, mirror, "Architect: keep it to one file.", to=("architect",))
    assert "keep it to one file" in await _grant_prompt(live_sessionmaker, runner, "architect")
    assert "keep it to one file" not in await _grant_prompt(live_sessionmaker, runner, "developer")


async def test_a_human_note_reaches_a_discussion_prompt_too(tmp_path, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)

    async def on_wake(role):
        await _respond(transport, mirror, role, "my view")

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mk_mode_config(), policy=RoundRobinFloorPolicy(), on_wake=on_wake)
    await _human_says(runner, mirror, "Please focus on cost.")
    prompt = await _grant_prompt(live_sessionmaker, runner, "a")
    assert "MESSAGE FROM THE HUMAN" in prompt and "Please focus on cost." in prompt


def test_platform_limit_detection_is_narrow():
    from orchestrator.moderator.runner import looks_like_platform_limit as f
    assert f("Claude usage limit reached. Your limit will reset at 5pm.")
    assert f("You've hit your weekly limit")
    assert not f("")
    assert not f(None)
    long_talk = "Should an API apply a rate limit per user or per key? " + "Consider fairness and abuse. " * 30
    assert not f(long_talk)
    assert not f("We should discuss the budget and the schedule.")


async def test_a_note_not_yet_mirrored_is_still_in_the_next_prompt(tmp_path, live_sessionmaker):
    """The race: the human's note is in the message log but the mirror has not polled yet. The grant syncs the
    mirror first, so the agent still gets the note."""
    session_id, runner, _ = await _build_session(live_sessionmaker, tmp_path, {"architect": ["ok\nVERDICT: planned"] * 2})
    from orchestrator.mirror import MessageMirror
    from orchestrator.pubsub import PubSub
    mirror = MessageMirror(FileTransport(tmp_path), live_sessionmaker, PubSub(), session_id=session_id)
    await runner._conversation.send(sender="human", to=["*"], kind="note", body="Use sqlite, not postgres.")  # no poll

    without_sync = await _grant_prompt(live_sessionmaker, runner, "architect")
    assert "Use sqlite" not in without_sync  # the old behaviour: the note was missed

    runner._mirror_sync = mirror.poll_once
    await runner._conversation.send(sender="human", to=["*"], kind="note", body="Also: keep it to one file.")  # no poll
    with_sync = await _grant_prompt(live_sessionmaker, runner, "architect")
    assert "keep it to one file" in with_sync


def test_cost_warnings_only_fire_when_spend_is_high():
    from orchestrator.moderator.runner import cost_warnings as w
    assert w(0.5, 1.0, None, False) == []                       # no dollar budget: nothing to compare with
    assert w(0.5, 1.0, 15.0, False) == []                       # small turn, small total
    big_turn = w(4.0, 5.0, 15.0, False)
    assert len(big_turn) == 1 and "$4.00" in big_turn[0] and "27%" in big_turn[0]
    total = w(0.2, 12.5, 15.0, False)
    assert len(total) == 1 and "83%" in total[0] and "stops when the budget is reached" in total[0]
    assert w(0.2, 13.0, 15.0, True) == []                       # the running-total warning is given once
    assert len(w(5.0, 14.0, 15.0, False)) == 2                   # both can apply to one turn


async def test_a_costly_turn_posts_a_cost_check_note(tmp_path, live_sessionmaker):
    """End to end: a budgeted session, a turn that cost 30% of the cap -> a moderator note appears."""
    session_id = await _make_session(live_sessionmaker, budget={"max_cost": 10.0})
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(roles={"moderator": None, "a": None})

    async def on_wake(role):
        await _respond(transport, mirror, role, "my view", meta={"tokens_in": 10, "tokens_out": 10, "cost": 3.0})

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id, mode=mode,
        policy=RoundRobinFloorPolicy(), on_wake=on_wake)
    await runner.run_once()
    await mirror.poll_once()
    async with live_sessionmaker() as session:
        notes = (await session.execute(select(MessageRow).where(
            MessageRow.session_id == session_id, MessageRow.kind == "note"))).scalars().all()
    assert any(n.body.startswith("Cost check:") and "$3.00" in n.body for n in notes)


async def test_blocked_architect_goes_again_with_the_humans_answer_after_resume(tmp_path, live_sessionmaker):
    """The 2026-10-19 live session: the architect said 'blocked' (no scope), the person could only press Resume, and
    every Resume paused again. Now an answer in the room sends the same role back in, with the answer in its prompt."""
    from orchestrator.mirror import MessageMirror
    from orchestrator.pubsub import PubSub

    session_id, runner, sandbox = await _build_session(live_sessionmaker, tmp_path, {
        "architect": ["I need the scope.\nVERDICT: blocked", "Plan: add hello().\nVERDICT: planned"],
        "developer": ["Done.\nVERDICT: implemented"],
        "qa-tester": ["```\npytest\n```\nexit 0\nVERDICT: pass"],
        "reviewer": ["Fine.\nVERDICT: approve"],
    })
    runner._mirror_sync = MessageMirror(FileTransport(tmp_path), live_sessionmaker, PubSub(), session_id=session_id).poll_once

    first = await _run_to_stop(runner)
    assert first.reason == "needs_human" and sandbox.woken == ["architect"]

    # Resume without an answer: it pauses again at once and does not wake anyone.
    async with live_sessionmaker() as db:
        (await db.get(Session, session_id)).status = "active"
        await db.commit()
    again = await _run_to_stop(runner)
    assert again.reason == "needs_human" and sandbox.woken == ["architect"]

    # The person answers (no mirror poll by hand), then resumes: the architect goes again and the build carries on.
    await runner._conversation.send(sender="human", to=["*"], kind="note", body="Scope: add a hello() function.")
    async with live_sessionmaker() as db:
        (await db.get(Session, session_id)).status = "active"
        await db.commit()
    final = await _run_to_stop(runner)
    assert sandbox.woken == ["architect", "architect", "developer", "qa-tester", "reviewer"]
    assert final.reason == "build-complete"


async def test_an_archived_session_is_left_out_of_the_agents_context(tmp_path, live_sessionmaker):
    """Archive means 'don't let this steer later sessions': neither its rolling summary nor its synthesis is given to
    the next session on the task. Unarchiving puts it back."""
    from datetime import datetime, timezone

    session_id, runner, _ = await _build_session(live_sessionmaker, tmp_path, {})
    async with live_sessionmaker() as db:
        first = await db.get(Session, session_id)
        old = Session(task_id=first.task_id, mode_id=first.mode_id, status="finished",
                      rolling_summary="They agreed on SQLITE-ONLY storage.")
        db.add(old)
        await db.flush()
        db.add(Artifact(session_id=old.id, type="synthesis", content_json={"decision": "use a queue, not cron"},
                        source_turn_ids=[]))
        await db.commit()
        old_id = old.id

    before = await runner._build_context(await runner._build_view())
    assert "SQLITE-ONLY" in before and "use a queue, not cron" in before

    async with live_sessionmaker() as db:
        (await db.get(Session, old_id)).archived_at = datetime.now(timezone.utc)
        await db.commit()
    archived = await runner._build_context(await runner._build_view())
    assert "SQLITE-ONLY" not in archived and "use a queue, not cron" not in archived

    async with live_sessionmaker() as db:
        (await db.get(Session, old_id)).archived_at = None
        await db.commit()
    assert "SQLITE-ONLY" in await runner._build_context(await runner._build_view())


async def test_task_text_includes_attached_context_as_reference_material(tmp_path, live_sessionmaker):
    from orchestrator.models import Session as SessionRow, TaskAttachment
    session_id = await _make_session(live_sessionmaker)
    await _set_task(live_sessionmaker, session_id, title="T", brief=None, output_type="memo", success_criteria=None)
    async with live_sessionmaker() as db:
        task_id = (await db.get(SessionRow, session_id)).task_id
        db.add(TaskAttachment(task_id=task_id, kind="file", name="terms.md", source=None, content="Data stays in the EU.", truncated=False))
        await db.commit()
    runner, *_ = mk_runner(tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
                           mode=mk_mode_config(), policy=RoundRobinFloorPolicy())
    text = await runner._task_text()
    assert text.startswith("Task: T\nExpected output: memo")
    assert "REFERENCE MATERIAL" in text and 'name="terms.md"' in text and "Data stays in the EU." in text


async def test_a_role_restricted_attachment_reaches_only_that_seat_in_discussion_and_build(tmp_path, live_sessionmaker):
    from orchestrator.models import Session as SessionRow, TaskAttachment
    session_id = await _make_session(live_sessionmaker)
    await _set_task(live_sessionmaker, session_id, title="T", brief=None, output_type="memo", success_criteria=None)
    await _seat(live_sessionmaker, session_id, "lawyer", 1)
    await _seat(live_sessionmaker, session_id, "developer", 2)
    async with live_sessionmaker() as db:
        task_id = (await db.get(SessionRow, session_id)).task_id
        db.add(TaskAttachment(task_id=task_id, kind="file", name="contract.md", source=None, content="PENALTY-CLAUSE-17 applies.",
                              truncated=False, roles=["lawyer"]))
        db.add(TaskAttachment(task_id=task_id, kind="file", name="notes.md", source=None, content="Shared notes.", truncated=False))
        await db.commit()
    for pipeline in (False, True):
        runner, *_ = mk_runner(tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
                               mode=mk_mode_config(), policy=RoundRobinFloorPolicy())
        runner._pipeline = pipeline
        view = await runner._build_view()
        runner._grant_seat = "lawyer"
        assert "PENALTY-CLAUSE-17" in await runner._build_context(view)
        runner._grant_seat = "developer"
        dev = await runner._build_context(view)
        assert "PENALTY-CLAUSE-17" not in dev and "contract.md" not in dev and "Shared notes." in dev
        runner._grant_seat = None
        nobody = await runner._build_context(view)
        assert "PENALTY-CLAUSE-17" not in nobody and "Shared notes." in nobody
    # all attachments hidden: no reference block at all
    runner._grant_seat = "developer"
    async with live_sessionmaker() as db:
        from sqlalchemy import update
        await db.execute(update(TaskAttachment).where(TaskAttachment.task_id == task_id).values(roles=["lawyer"]))
        await db.commit()
    assert "REFERENCE MATERIAL" not in await runner._task_text()


async def test_the_final_write_up_sees_only_open_attachments_even_after_a_restricted_seat_spoke(tmp_path, live_sessionmaker):
    """_finish must not build the write-up as the last speaker: that seat's restricted files would leak into the
    artifact every seat and the reader get. The hint handed to the synthesizer carries open attachments only."""
    from orchestrator.models import Session as SessionRow, TaskAttachment
    session_id = await _make_session(live_sessionmaker)
    await _set_task(live_sessionmaker, session_id, title="T", brief=None, output_type="memo", success_criteria=None)
    await _seat(live_sessionmaker, session_id, "lawyer", 1)
    async with live_sessionmaker() as db:
        task_id = (await db.get(SessionRow, session_id)).task_id
        db.add(TaskAttachment(task_id=task_id, kind="file", name="contract.md", source=None,
                              content="PENALTY-CLAUSE-17 applies.", truncated=False, roles=["lawyer"]))
        db.add(TaskAttachment(task_id=task_id, kind="file", name="notes.md", source=None,
                              content="Shared notes.", truncated=False))
        await db.commit()
    runner, *_ = mk_runner(tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
                           mode=mk_mode_config(), policy=RoundRobinFloorPolicy())
    hints = []
    real = runner._synthesizer.synthesize

    async def spy(view, summary, turns, synthesis_prompt_hint=None, **kw):
        hints.append(synthesis_prompt_hint or "")
        return await real(view, summary, turns, synthesis_prompt_hint=synthesis_prompt_hint, **kw)

    runner._synthesizer.synthesize = spy
    runner._grant_seat = "lawyer"  # the restricted seat spoke last
    assert "PENALTY-CLAUSE-17" in await runner._task_text()  # sanity: that seat does see its file
    await runner._finish(await runner._build_view(), "max-rounds")
    assert len(hints) == 1
    assert "Shared notes." in hints[0] and "PENALTY-CLAUSE-17" not in hints[0] and "contract.md" not in hints[0]

async def _file_transport_turn_events(tmp_path, live_sessionmaker, *, attachments):
    """Runs one file-transport turn (the sandbox writes the answer envelope) for seat "a" and returns
    (events published during it, stored answer meta)."""
    from orchestrator.models import TaskAttachment
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    async with live_sessionmaker() as db:
        task_id = (await db.get(Session, session_id)).task_id
        for name, text in attachments:
            db.add(TaskAttachment(task_id=task_id, kind="file", name=name, source=None, content=text, truncated=False))
        await db.commit()

    async def on_wake(role):
        await _respond(transport, mirror, role, "my view", meta={"tokens_in": 1})

    runner, transport, mirror, _ = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mk_mode_config(), policy=RoundRobinFloorPolicy(), on_wake=on_wake)
    queue = runner._pubsub.subscribe(bounded=False)
    await runner.run_once()
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    async with live_sessionmaker() as db:
        row = (await db.execute(select(MessageRow).where(MessageRow.kind == "answer"))).scalars().one()
    return events, row


async def test_a_file_transport_turn_publishes_the_read_report_live(tmp_path, live_sessionmaker):
    secret = "SECRET-TEXT-NEVER-IN-EVENT"
    events, row = await _file_transport_turn_events(
        tmp_path, live_sessionmaker, attachments=[("notes.md", f"Shared notes. {secret}")])
    patched = [e for e in events if e["type"] == "message_meta"]
    assert len(patched) == 1
    data = patched[0]["data"]
    assert data["message_id"] == row.message_id and data["from"] == "a"
    assert data["meta"]["attachments"] == row.meta["attachments"]  # live and stored (reload) agree
    assert [(r["name"], r["mode"]) for r in data["meta"]["attachments"]] == [("notes.md", "whole")]
    assert secret not in json.dumps(patched)  # names, modes and passage numbers only
    # it comes after the message event the room already has, never before
    kinds = [e["type"] for e in events]
    assert kinds.index("message") < kinds.index("message_meta")


async def test_a_turn_with_no_attachments_publishes_no_meta_event(tmp_path, live_sessionmaker):
    events, row = await _file_transport_turn_events(tmp_path, live_sessionmaker, attachments=[])
    assert not [e for e in events if e["type"] == "message_meta"]
    assert "attachments" not in (row.meta or {})


async def test_the_live_report_leaves_out_attachments_hidden_from_the_seat(tmp_path, live_sessionmaker):
    from orchestrator.models import TaskAttachment
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    async with live_sessionmaker() as db:
        task_id = (await db.get(Session, session_id)).task_id
        db.add(TaskAttachment(task_id=task_id, kind="file", name="contract.md", source=None, content="x", truncated=False, roles=["lawyer"]))
        db.add(TaskAttachment(task_id=task_id, kind="file", name="notes.md", source=None, content="y", truncated=False))
        await db.commit()

    async def on_wake(role):
        await _respond(transport, mirror, role, "my view")

    runner, transport, mirror, _ = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mk_mode_config(), policy=RoundRobinFloorPolicy(), on_wake=on_wake)
    queue = runner._pubsub.subscribe(bounded=False)
    await runner.run_once()
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    patched = [e for e in events if e["type"] == "message_meta"]
    assert [r["name"] for r in patched[0]["data"]["meta"]["attachments"]] == ["notes.md"]
    assert "contract.md" not in json.dumps(patched)
