"""M2.9: ModeratorRunner resume correctness across a simulated crash/restart -- two
separate ModeratorRunner instances against the same real Postgres + real tmp-dir factory
directory, simulating pre- and post-restart processes. This needs live Postgres and a real
filesystem tmp-dir (FileTransport does real file I/O) -- no way to fake this meaningfully.
No live sbx CLI or Anthropic credentials needed (same disclosed gap as always).

Reuses test_moderator_runner.py's fixtures/helpers (mk_runner, _make_session, _seat, etc.)
rather than duplicating them.
"""
import uuid

from acp.models import Envelope
from orchestrator.floor import RoundRobinFloorPolicy
from orchestrator.models import Message, Turn
from sqlalchemy import select

from test_moderator_runner import CONV_ID, _make_session, _respond, _seat, mk_mode_config, mk_runner


async def _simulate_crashed_assignment(transport, mirror, participant: str, body: str = "go") -> None:
    """Mimics exactly what ModeratorRunner._handle_grant does up to (and including) the
    durable conversation.send() -- and NOTHING after it (no wake_role, no response) -- to
    deterministically simulate a process crash in that exact window, rather than trying to
    interrupt a real runner mid-flight with timing tricks."""
    transport.send(Envelope(conversation_id=CONV_ID, from_="moderator", to=[participant],
                             kind="assignment", body=body))
    await mirror.poll_once()


async def test_resume_detects_a_pending_grant_and_re_wakes_rather_than_computing_a_new_decision(
        tmp_path, live_sessionmaker):
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    await _seat(live_sessionmaker, session_id, "b", 1)
    mode = mk_mode_config(roles={"moderator": None, "a": None, "b": None})

    # "Runner A" (pre-crash): only ever gets as far as sending the assignment -- it is
    # then discarded WITHOUT any graceful shutdown, simulating a crash right after send()
    # succeeded but before wake_role() or any response.
    runner_a, transport, mirror, sandbox_a = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(),
    )
    await _simulate_crashed_assignment(transport, mirror, "a")

    # "Runner B" (post-restart): a fresh instance -- fresh in-memory state
    # (_opening_done=False etc.) -- pointed at the SAME session_id/DB/factory-dir, but with
    # its OWN mirror/pubsub (a fresh PubSub is exactly what a real restart would produce;
    # see docs/decisions.md's M2.8 entry on per-session PubSub). `on_wake` must publish
    # through runner B's own mirror, not runner A's -- otherwise the response would land on
    # a pubsub nobody's listening to.
    responses = iter(["a's real response"])
    b_box = {}

    async def on_wake(role):
        await _respond(b_box["transport"], b_box["mirror"], role, next(responses))

    runner_b, transport_b, mirror_b, sandbox_b = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(), on_wake=on_wake,
    )
    b_box["transport"], b_box["mirror"] = transport_b, mirror_b

    result = await runner_b.run_once()

    assert result.stopped is False
    assert sandbox_b.woken == ["a"]  # re-woken (idempotent, at-least-once), not re-granted to someone else
    async with live_sessionmaker() as db:
        turns = (await db.execute(
            select(Turn).where(Turn.session_id == session_id).order_by(Turn.seq)
        )).scalars().all()
    assert [t.content for t in turns] == ["a's real response"]

    # The assignment was never re-sent -- only the one from before the "crash" exists.
    async with live_sessionmaker() as db:
        assignments = (await db.execute(
            select(Message).where(Message.session_id == session_id, Message.kind == "assignment")
        )).scalars().all()
    assert len(assignments) == 1


async def test_resume_after_a_pending_grant_resolves_proceeds_deterministically(tmp_path, live_sessionmaker):
    """Once the pre-crash pending grant is resolved, the runner's next behavior must match
    what a single continuous runner would have produced from equivalent state: round-robin's
    opening pass continues to "b" next, exactly as if nothing had ever crashed."""
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    await _seat(live_sessionmaker, session_id, "b", 1)
    mode = mk_mode_config(roles={"moderator": None, "a": None, "b": None})

    runner_a, transport, mirror, _ = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(),
    )
    await _simulate_crashed_assignment(transport, mirror, "a")

    responses = iter(["a responds", "b responds"])
    b_box = {}

    async def on_wake(role):
        await _respond(b_box["transport"], b_box["mirror"], role, next(responses))

    runner_b, transport_b, mirror_b, sandbox_b = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(), on_wake=on_wake,
    )
    b_box["transport"], b_box["mirror"] = transport_b, mirror_b

    await runner_b.run_once()  # resumes the pending grant to "a"
    await runner_b.run_once()  # continues the (still-incomplete) opening pass to "b"

    assert sandbox_b.woken == ["a", "b"]
    async with live_sessionmaker() as db:
        turns = (await db.execute(
            select(Turn).where(Turn.session_id == session_id).order_by(Turn.seq)
        )).scalars().all()
    assert [t.content for t in turns] == ["a responds", "b responds"]


async def test_a_normally_resolved_grant_is_never_mistaken_for_pending_on_the_next_call(
        tmp_path, live_sessionmaker):
    """Sanity check for the detection logic itself: once a grant has a real response,
    re-running _pending_grant() must return None, not re-detect the already-answered
    assignment -- otherwise every ordinary (non-crashed) iteration would loop forever."""
    session_id = await _make_session(live_sessionmaker)
    await _seat(live_sessionmaker, session_id, "a", 0)
    mode = mk_mode_config(roles={"moderator": None, "a": None})

    async def on_wake(role):
        await _respond(transport, mirror, role, "answered fine")

    runner, transport, mirror, sandbox = mk_runner(
        tmp_path=tmp_path, live_sessionmaker=live_sessionmaker, session_id=session_id,
        mode=mode, policy=RoundRobinFloorPolicy(), on_wake=on_wake,
    )
    await runner.run_once()  # sends the assignment, gets the response, records the turn

    assert await runner._pending_grant() is None
