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
from sqlalchemy import select

from orchestrator.conversation import OrchestratorConversation
from orchestrator.floor import RaiseHandFloorPolicy, RoundRobinFloorPolicy, StopRulesConfig
from orchestrator.mirror import MessageMirror
from orchestrator.models import Agent, Artifact, Mode, Session, SessionAgent, Task, Turn
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
    session_id = await _make_session(live_sessionmaker, budget={"max_tokens": 100})
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(roles={"moderator": None, "a": None})
    async with live_sessionmaker() as session:
        agent_id = (await session.execute(select(Agent.id).where(Agent.role == "a"))).scalar_one()
        session.add(Turn(session_id=session_id, seq=1, speaker_id=agent_id, round=1, content="x",
                          tokens_in=60, tokens_out=50))
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
