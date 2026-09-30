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
              summaries=None, synthesis=None, scorer=None, stop_rules=None) -> tuple:
    conversation = OrchestratorConversation.create(conversation_id=CONV_ID, factory_dir=tmp_path, mode=mode)
    transport = FileTransport(tmp_path)
    pubsub = PubSub()
    mirror = MessageMirror(transport, live_sessionmaker, pubsub, session_id=session_id)
    sandbox_provider = FakeSandboxProvider(on_wake=on_wake)
    runner = ModeratorRunner(
        session_id=session_id, sessionmaker=live_sessionmaker, conversation=conversation,
        sandbox_provider=sandbox_provider, sandbox=fake_sandbox(), mode=mode, policy=policy,
        stop_rules=stop_rules or StopRulesConfig(), pubsub=pubsub, personas=[],
        summarizer=FakeSummarizer(summaries or [SummaryResult(summary="s", has_new_argument=True) for _ in range(20)]),
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
    assert [a.type for a in artifacts] == ["synthesis"]


async def test_budget_exceeded_stops_the_session(tmp_path, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker, budget={"max_tokens": 100})
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(roles={"moderator": None, "a": None})
    async with live_sessionmaker() as session:
        agent_id = (await session.execute(select(Agent.id).where(Agent.role == "a"))).scalar_one()
        session.add(Turn(session_id=session_id, seq=1, speaker_id=agent_id, round=1, content="x",
                          tokens_in=60, tokens_out=50))
        await session.commit()

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(),
    )
    result = await runner.run_once()

    assert result.stopped is True and result.reason == "budget-exceeded"


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
