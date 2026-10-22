"""M2.8: REST API session lifecycle -- create, start, interject, stop, SSE. Real Postgres
for everything touching Task/Session/SessionAgent/Mode/Turn/Artifact rows. SandboxProvider
and the LLM seams are swapped for fakes via app.dependency_overrides (no live sbx CLI or
Anthropic credentials needed or assumed available in this dev-team sandbox -- the same
compound gap M2.4/M2.6/M2.7 already disclosed; see docs/decisions.md).

`_auto_responder` simulates a participant's eventual response the real way: writing an
Envelope through the session's actual FileTransport (found via BAYTO_FACTORY_ROOT), so the
already-running MessageMirror picks it up and publishes it -- not a shortcut that bypasses
the real plumbing.
"""
import asyncio
from datetime import datetime, timezone
import json
import uuid
from pathlib import Path

import httpx
import pytest
from acp.models import Envelope
from acp.transport import FileTransport
from sqlalchemy import select

from fakes import FakeSandboxProvider
from orchestrator.api.deps import get_hand_raise_scorer_factory, get_sandbox_provider, get_summarizer, get_synthesizer
from orchestrator.app import app
from orchestrator.models import Agent, Artifact, Mode, Session, SessionAgent, Task, Turn
from orchestrator.moderator import FakeSummarizer, FakeSynthesizer, SummaryResult, SynthesisResult

FIXTURES = Path(__file__).parent / "fixtures" / "modes"


@pytest.fixture(autouse=True)
async def _wire_test_env(monkeypatch, tmp_path, live_sessionmaker):
    """Depends on `live_sessionmaker` (even though the fixture body doesn't use it
    directly) purely to sequence teardown: pytest tears fixtures down in reverse
    dependency order, so this runs (and cancels any still-running mirror/runner tasks)
    BEFORE live_schema's per-test `alembic downgrade base`. Without that ordering, a
    leaked background task from one test keeps polling the just-dropped tables during the
    NEXT test, throwing UndefinedTable errors (and, worse, potentially deadlocking a test
    waiting on a status change that will now never come)."""
    monkeypatch.setenv("BAYTO_MODES_DIR", str(FIXTURES))
    monkeypatch.setenv("BAYTO_FACTORY_ROOT", str(tmp_path / "sessions"))
    app.state.running_sessions.clear()
    app.state.session_runtimes.clear()
    yield
    app.dependency_overrides.clear()
    for runtime in app.state.session_runtimes.values():
        runtime.mirror_task.cancel()
        runtime.runner_task.cancel()
    for runtime in app.state.session_runtimes.values():
        for task in (runtime.mirror_task, runtime.runner_task):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
    app.state.running_sessions.clear()
    app.state.session_runtimes.clear()


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _many_summaries():
    return [SummaryResult(summary="s", has_new_argument=True) for _ in range(50)]


def _wire_fakes(sandbox_provider, synthesis=None):
    app.dependency_overrides[get_sandbox_provider] = lambda: sandbox_provider
    app.dependency_overrides[get_summarizer] = lambda: FakeSummarizer(_many_summaries())
    app.dependency_overrides[get_synthesizer] = lambda: FakeSynthesizer(
        synthesis or SynthesisResult(content_json={"ok": True}, source_turn_ids=[]))
    app.dependency_overrides[get_hand_raise_scorer_factory] = lambda: (lambda: None)


async def _seed(sessionmaker, *, mode_name="round-robin-test", roles=("a", "b")) -> tuple:
    async with sessionmaker() as db:
        task = Task(title="t", output_type="text")
        mode = Mode(name=mode_name, phases_json={}, stop_rules_json={})
        agents = [Agent(kind="native", name=r, role=r) for r in roles]
        db.add_all([task, mode, *agents])
        await db.commit()
        return task.id, mode.id, [a.id for a in agents]


def _auto_responder(factory_root: Path, session_id_box: dict, meta=None):
    async def on_wake(role):
        transport = FileTransport(factory_root / str(session_id_box["id"]))
        transport.send(Envelope(conversation_id=str(session_id_box["id"]), from_=role, to=["moderator"],
                                 kind="answer", body=f"{role} response",
                                 meta=meta or {"tokens_in": 1, "tokens_out": 1, "cost": 0.0001}))
    return on_wake


async def _wait_for_status(sessionmaker, session_id: uuid.UUID, want: str, timeout: float = 5.0) -> Session:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    row = None
    while loop.time() < deadline:
        async with sessionmaker() as db:
            row = await db.get(Session, session_id)
            if row.status == want:
                return row
        await asyncio.sleep(0.02)
    raise AssertionError(f"session {session_id} never reached status {want!r} (last seen: {row.status!r})")


async def _wait_for_mirrored_message(sessionmaker, session_id: uuid.UUID, kind: str, timeout: float = 5.0):
    """The mirror polls on its own cadence (MessageMirror.run_forever's default 1.0s
    interval); a message written via conversation.send() isn't necessarily in Postgres
    yet by the time this returns."""
    from orchestrator.models import Message
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        async with sessionmaker() as db:
            rows = (await db.execute(
                select(Message).where(Message.session_id == session_id, Message.kind == kind)
            )).scalars().all()
            if rows:
                return rows
        await asyncio.sleep(0.02)
    raise AssertionError(f"no {kind!r} message mirrored for session {session_id} in time")


# ---------------------------------------------------------------- POST /sessions

async def test_create_session_snapshots_agent_version_and_returns_created(client, live_sessionmaker):
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)

    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0]), "seat_order": 0},
                   {"agent_id": str(agent_ids[1]), "seat_order": 1}],
        "budget": {"tokens": 100000, "dollars": 5.0},
    })

    assert resp.status_code == 201
    body = resp.json()
    assert body["session"]["status"] == "created"
    assert body["session"]["started_at"] is None
    assert body["session"]["budget"] == {"max_tokens": 100000, "max_cost": 5.0}
    assert {sa["agent_version"] for sa in body["session_agents"]} == {"1"}  # Agent.version's default


async def test_an_archived_agent_cannot_be_put_in_a_new_session(client, live_sessionmaker):
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    async with live_sessionmaker() as db:
        row = await db.get(Agent, agent_ids[0])
        row.archived_at = datetime.now(timezone.utc)
        await db.commit()
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0]), "seat_order": 0}, {"agent_id": str(agent_ids[1]), "seat_order": 1}],
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"]["code"] == "invalid_agent"


@pytest.mark.parametrize("budget", [{"tokens": -5}, {"tokens": 0}, {"dollars": -1.0}, {"dollars": 0}])
async def test_create_session_rejects_a_non_positive_budget(client, live_sessionmaker, budget):
    """M2.10: budget: {tokens: -5} or {tokens: 0} must not be silently accepted, only to
    surface later as a nonsensical comparison inside the stop-check."""
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0])}],
        "budget": budget,
    })
    assert resp.status_code == 422


async def test_create_session_rejects_an_unknown_task(client, live_sessionmaker):
    _, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(uuid.uuid4()), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0])}],
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"]["code"] == "invalid_task"


async def test_create_session_rejects_an_unknown_mode(client, live_sessionmaker):
    task_id, _, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(uuid.uuid4()),
        "roster": [{"agent_id": str(agent_ids[0])}],
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"]["code"] == "invalid_mode"


async def test_create_session_rejects_a_mode_row_whose_yaml_file_is_missing(client, live_sessionmaker):
    task_id, _, agent_ids = await _seed(live_sessionmaker, mode_name="no-such-mode-file")
    async with live_sessionmaker() as db:
        mode_row = (await db.execute(select(Mode).where(Mode.name == "no-such-mode-file"))).scalar_one()
        mode_id = mode_row.id

    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0])}],
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"]["code"] == "invalid_mode"


async def test_create_session_rejects_an_unknown_agent(client, live_sessionmaker):
    task_id, mode_id, _ = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(uuid.uuid4())}],
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"]["code"] == "invalid_agent"


# ---------------------------------------------------------------- POST /sessions/{id}/start

async def test_start_runs_the_session_to_completion(client, live_sessionmaker, tmp_path):
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0]), "seat_order": 0},
                   {"agent_id": str(agent_ids[1]), "seat_order": 1}],
    })
    session_id = uuid.UUID(resp.json()["session"]["id"])
    box = {"id": session_id}

    sandbox_provider = FakeSandboxProvider(on_wake=_auto_responder(tmp_path / "sessions", box))
    _wire_fakes(sandbox_provider)

    resp = await client.post(f"/sessions/{session_id}/start")
    assert resp.status_code == 202
    assert resp.json()["session"]["status"] == "starting"

    row = await _wait_for_status(live_sessionmaker, session_id, "finished")
    assert row.started_at is not None
    assert row.ended_at is not None
    assert sandbox_provider.created_for == [task_id]
    assert sandbox_provider.started_teams == [f"sbx-{task_id}"]

    async with live_sessionmaker() as db:
        turns = (await db.execute(select(Turn).where(Turn.session_id == session_id).order_by(Turn.seq))).scalars().all()
        artifacts = (await db.execute(select(Artifact).where(Artifact.session_id == session_id))).scalars().all()
    # round-robin-test.yaml: max_rounds=2 -- opening (a, b) then one more grant (a) hits it
    assert [t.content for t in turns] == ["a response", "b response", "a response"]
    assert [a.type for a in artifacts] == ["synthesis"]


async def test_start_on_an_already_started_session_is_conflict(client, live_sessionmaker, tmp_path):
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0])}],
    })
    session_id = resp.json()["session"]["id"]

    _wire_fakes(FakeSandboxProvider())
    resp = await client.post(f"/sessions/{session_id}/start")
    assert resp.status_code == 202

    resp2 = await client.post(f"/sessions/{session_id}/start")
    assert resp2.status_code == 409
    assert resp2.json()["detail"]["error"]["code"] == "invalid_transition"


async def test_start_on_a_nonexistent_session_is_not_found(client, live_sessionmaker):
    _wire_fakes(FakeSandboxProvider())
    resp = await client.post(f"/sessions/{uuid.uuid4()}/start")
    assert resp.status_code == 404


# ---------------------------------------------------------------- POST /sessions/{id}/interject

async def test_interject_on_a_running_session_lands_in_the_transcript(client, live_sessionmaker, tmp_path):
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0]), "seat_order": 0},
                   {"agent_id": str(agent_ids[1]), "seat_order": 1}],
    })
    session_id = uuid.UUID(resp.json()["session"]["id"])
    box = {"id": session_id}

    # Slow responder so the session stays "active" long enough to interject into it.
    responded = asyncio.Event()

    async def on_wake(role):
        await asyncio.sleep(0.2)
        await _auto_responder(tmp_path / "sessions", box)(role)
        responded.set()

    sandbox_provider = FakeSandboxProvider(on_wake=on_wake)
    _wire_fakes(sandbox_provider)
    await client.post(f"/sessions/{session_id}/start")

    for _ in range(100):
        if session_id in app.state.session_runtimes:
            break
        await asyncio.sleep(0.02)

    resp = await client.post(f"/sessions/{session_id}/interject", json={"body": "hurry up", "to": ["a"]})
    assert resp.status_code == 200
    envelope = resp.json()["envelope"]
    assert envelope["from"] == "human"
    assert envelope["to"] == ["a"]
    assert envelope["body"] == "hurry up"

    await _wait_for_status(live_sessionmaker, session_id, "finished", timeout=10.0)
    async with live_sessionmaker() as db:
        from orchestrator.models import Message
        rows = (await db.execute(select(Message).where(Message.session_id == session_id,
                                                          Message.from_ == "human"))).scalars().all()
    assert [r.body for r in rows] == ["hurry up"]


async def test_interject_before_start_is_conflict(client, live_sessionmaker):
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0])}],
    })
    session_id = resp.json()["session"]["id"]

    resp = await client.post(f"/sessions/{session_id}/interject", json={"body": "hi"})
    assert resp.status_code == 409


async def test_round_robin_interject_does_not_reorder_the_rotation(client, live_sessionmaker, tmp_path):
    """Acceptance criterion (product-owner-confirmed): an interject must not change which
    participant next_seat() would have granted anyway."""
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0]), "seat_order": 0},
                   {"agent_id": str(agent_ids[1]), "seat_order": 1}],
    })
    session_id = uuid.UUID(resp.json()["session"]["id"])
    box = {"id": session_id}
    interjected = {"done": False}

    async def on_wake(role):
        # Interject exactly once, right after the first response, before the second grant.
        await _auto_responder(tmp_path / "sessions", box)(role)
        if not interjected["done"]:
            interjected["done"] = True
            await client.post(f"/sessions/{session_id}/interject", json={"body": "fyi", "to": ["b"]})

    sandbox_provider = FakeSandboxProvider(on_wake=on_wake)
    _wire_fakes(sandbox_provider)
    await client.post(f"/sessions/{session_id}/start")

    await _wait_for_status(live_sessionmaker, session_id, "finished", timeout=10.0)
    async with live_sessionmaker() as db:
        turns = (await db.execute(select(Turn).where(Turn.session_id == session_id).order_by(Turn.seq))).scalars().all()
    # Same order as the no-interject case (test_start_runs_the_session_to_completion): a, b, a.
    assert [t.content for t in turns] == ["a response", "b response", "a response"]


# ---------------------------------------------------------------- POST /sessions/{id}/stop

async def test_stop_with_synthesize_true_runs_synthesis(client, live_sessionmaker, tmp_path):
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0]), "seat_order": 0}],
    })
    session_id = uuid.UUID(resp.json()["session"]["id"])
    box = {"id": session_id}

    # Slow responder: gives /stop time to flip the status while the opening grant is still
    # in flight, so the runner's *next* iteration (right after this response) sees it --
    # a session truly stuck waiting the full 300s default grant timeout would make this
    # test itself take minutes, which is what the real "worst case" latency means here.
    async def on_wake(role):
        await asyncio.sleep(0.2)
        await _auto_responder(tmp_path / "sessions", box)(role)

    _wire_fakes(FakeSandboxProvider(on_wake=on_wake))
    await client.post(f"/sessions/{session_id}/start")
    await _wait_for_status(live_sessionmaker, session_id, "active")

    resp = await client.post(f"/sessions/{session_id}/stop", json={"synthesize": True})
    assert resp.status_code == 202
    assert resp.json()["session"]["status"] == "stopping"

    await _wait_for_status(live_sessionmaker, session_id, "finished", timeout=10.0)
    async with live_sessionmaker() as db:
        artifacts = (await db.execute(select(Artifact).where(Artifact.session_id == session_id))).scalars().all()
    assert [a.type for a in artifacts] == ["synthesis"]


async def test_stop_with_synthesize_false_skips_synthesis(client, live_sessionmaker, tmp_path):
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0]), "seat_order": 0}],
    })
    session_id = uuid.UUID(resp.json()["session"]["id"])
    box = {"id": session_id}

    async def on_wake(role):
        await asyncio.sleep(0.2)
        await _auto_responder(tmp_path / "sessions", box)(role)

    _wire_fakes(FakeSandboxProvider(on_wake=on_wake))
    await client.post(f"/sessions/{session_id}/start")
    await _wait_for_status(live_sessionmaker, session_id, "active")

    resp = await client.post(f"/sessions/{session_id}/stop", json={"synthesize": False})
    assert resp.status_code == 202
    assert resp.json()["session"]["status"] == "cancelling"

    await _wait_for_status(live_sessionmaker, session_id, "finished", timeout=10.0)
    async with live_sessionmaker() as db:
        artifacts = (await db.execute(select(Artifact).where(Artifact.session_id == session_id))).scalars().all()
    assert artifacts == []


async def test_stop_records_a_durable_x_stop_message_with_synthesize_and_reason(client, live_sessionmaker, tmp_path):
    """M2.9: `synthesize` is already durable via the stopping/cancelling status split
    itself (see docs/decisions.md), but `reason` was accepted by /stop and never persisted
    anywhere until this -- the actual new behavior the x-stop message closes."""
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0]), "seat_order": 0}],
    })
    session_id = uuid.UUID(resp.json()["session"]["id"])
    box = {"id": session_id}

    async def on_wake(role):
        await asyncio.sleep(0.2)
        await _auto_responder(tmp_path / "sessions", box)(role)

    _wire_fakes(FakeSandboxProvider(on_wake=on_wake))
    await client.post(f"/sessions/{session_id}/start")
    await _wait_for_status(live_sessionmaker, session_id, "active")

    resp = await client.post(f"/sessions/{session_id}/stop", json={"synthesize": False, "reason": "budget exhausted"})
    assert resp.status_code == 202

    rows = await _wait_for_mirrored_message(live_sessionmaker, session_id, "x-stop")
    assert len(rows) == 1
    assert rows[0].from_ == "human"
    assert json.loads(rows[0].body) == {"synthesize": False, "reason": "budget exhausted"}

    await _wait_for_status(live_sessionmaker, session_id, "finished", timeout=10.0)


async def test_stop_with_no_live_runtime_still_updates_status_and_warns(client, live_sessionmaker, tmp_path, caplog):
    """The defensive fallback: a session can be active/needs_human in the DB with no
    app.state.session_runtimes entry (e.g. a narrow restart-window edge case). /stop must
    still flip the status -- it just can't durably record `reason` without a live
    OrchestratorConversation to send through."""
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0]), "seat_order": 0}],
    })
    session_id = uuid.UUID(resp.json()["session"]["id"])
    box = {"id": session_id}

    async def on_wake(role):
        await asyncio.sleep(0.2)
        await _auto_responder(tmp_path / "sessions", box)(role)

    _wire_fakes(FakeSandboxProvider(on_wake=on_wake))
    await client.post(f"/sessions/{session_id}/start")
    await _wait_for_status(live_sessionmaker, session_id, "active")
    hidden = app.state.session_runtimes.pop(session_id)  # simulate the no-live-runtime edge case
    try:
        import logging
        with caplog.at_level(logging.WARNING):
            resp = await client.post(f"/sessions/{session_id}/stop", json={"synthesize": True})
        assert resp.status_code == 202
        assert resp.json()["session"]["status"] == "stopping"
        assert any("no live runtime" in r.message for r in caplog.records)
    finally:
        # Put it back so the fixture's teardown can cancel its mirror/runner tasks. Left out, they keep running
        # and polling, hold a transaction, and the next test's `DROP TABLE` waits on them forever.
        app.state.session_runtimes[session_id] = hidden

    async with live_sessionmaker() as db:
        row = await db.get(Session, session_id)
    assert row.status == "stopping"  # the status update itself does not depend on a live runtime


async def test_stop_a_session_that_was_never_started_is_conflict(client, live_sessionmaker):
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0])}],
    })
    session_id = resp.json()["session"]["id"]

    resp = await client.post(f"/sessions/{session_id}/stop", json={})
    assert resp.status_code == 409


# ---------------------------------------------------------------- GET /sessions/{id}/events (SSE)

async def test_sse_replays_backlog_since_seq(client, live_sessionmaker, tmp_path):
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0]), "seat_order": 0}],
    })
    session_id = uuid.UUID(resp.json()["session"]["id"])
    box = {"id": session_id}

    sandbox_provider = FakeSandboxProvider(on_wake=_auto_responder(tmp_path / "sessions", box))
    _wire_fakes(sandbox_provider)
    await client.post(f"/sessions/{session_id}/start")
    await _wait_for_status(live_sessionmaker, session_id, "finished")

    async def _read_backlog():
        lines = []
        async with client.stream("GET", f"/sessions/{session_id}/events?since_seq=0") as resp:
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    lines.append(json.loads(line[len("data:"):].strip()))
                if len(lines) >= 2:  # the assignment + the response
                    break
        return lines

    # The session already finished (runner_task.done()), so the handler returns right
    # after the backlog replay -- this should complete well within a couple of seconds;
    # bounded defensively in case of an unrelated regression that reopens the live tail.
    lines = await asyncio.wait_for(_read_backlog(), timeout=5.0)
    kinds = [e["kind"] for e in lines]
    assert "assignment" in kinds and "answer" in kinds


async def test_sse_emits_rolling_summary_events(client, live_sessionmaker, tmp_path):
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0]), "seat_order": 0}],
    })
    session_id = uuid.UUID(resp.json()["session"]["id"])
    box = {"id": session_id}

    # The FakeSummarizer is pre-configured to return 's' 50 times in _wire_fakes.
    # The auto responder will cause a wake, turning into a message, which triggers summarize.
    sandbox_provider = FakeSandboxProvider(on_wake=_auto_responder(tmp_path / "sessions", box))
    _wire_fakes(sandbox_provider)

    await client.post(f"/sessions/{session_id}/start")

    # Connect right after /start, no since_seq. The runtime is registered a moment later; the
    # handler waits for it, and the stream ends with an `end` event once the runner is done.
    async def _read_live():
        events, name = [], None
        async with client.stream("GET", f"/sessions/{session_id}/events") as resp:
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    name = line[len("event:"):].strip()
                elif line.startswith("data:") and name == "rolling_summary":
                    events.append(json.loads(line[len("data:"):].strip()))
                elif not line.strip():
                    name = None
        return events

    events = await asyncio.wait_for(_read_live(), timeout=10.0)
    assert len(events) >= 1  # live update(s) and/or the persisted snapshot sent on connect
    assert events[0]["summary"] == "s"

    await _wait_for_status(live_sessionmaker, session_id, "finished")


async def test_sse_messages_carry_ids_and_last_event_id_resumes(client, live_sessionmaker, tmp_path):
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0]), "seat_order": 0}],
    })
    session_id = uuid.UUID(resp.json()["session"]["id"])
    sandbox_provider = FakeSandboxProvider(on_wake=_auto_responder(tmp_path / "sessions", {"id": session_id}))
    _wire_fakes(sandbox_provider)
    await client.post(f"/sessions/{session_id}/start")
    await _wait_for_status(live_sessionmaker, session_id, "finished")

    async def _read(headers=None, query=""):
        ids, datas = [], []
        async with client.stream("GET", f"/sessions/{session_id}/events{query}", headers=headers or {}) as r:
            async for line in r.aiter_lines():
                if line.startswith("id:"):
                    ids.append(int(line[3:].strip()))
                elif line.startswith("data:") and "message_id" in line:
                    datas.append(json.loads(line[5:].strip()))
        return ids, datas

    ids, datas = await asyncio.wait_for(_read(query="?since_seq=0"), timeout=5.0)
    assert ids == sorted(ids) and len(ids) >= 2 and [d["seq"] for d in datas] == ids

    # An automatic browser reconnect sends Last-Event-ID instead of since_seq: only newer messages come back.
    later_ids, _ = await asyncio.wait_for(_read(headers={"Last-Event-ID": str(ids[0])}), timeout=5.0)
    assert later_ids == ids[1:]


async def test_sse_returns_nothing_live_for_a_session_that_was_never_started(client, live_sessionmaker):
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0])}],
    })
    session_id = resp.json()["session"]["id"]

    async def _read_all():
        body = b""
        async with client.stream("GET", f"/sessions/{session_id}/events") as resp:
            assert resp.status_code == 200
            async for chunk in resp.aiter_bytes():
                body += chunk
        return body

    body = await asyncio.wait_for(_read_all(), timeout=5.0)
    assert b"event: end" in body  # nothing live: a final `end` so the client stops reconnecting
    assert b"data: {\"protocol\"" not in body  # and no messages


# ---------------------------------------------------------------- pause/resume keeps one live mirror

async def test_resume_retires_the_old_mirror_and_the_discussion_continues(client, live_sessionmaker, tmp_path):
    """Resume relaunches the runner with a fresh mirror. The old mirror used to keep running, racing the
    new one for every reply; only the winner publishes, so the resumed runner could wait out its whole
    grant timeout for an answer that had already arrived (the session then never finished, and /stop sat
    in `stopping`). Found by driving pause -> resume -> stop in the real app."""
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(a), "seat_order": i} for i, a in enumerate(agent_ids)],
    })
    session_id = uuid.UUID(resp.json()["session"]["id"])
    base = _auto_responder(tmp_path / "sessions", {"id": session_id})

    async def slow(role):  # leave a window in which to pause
        await asyncio.sleep(0.3)
        await base(role)

    _wire_fakes(FakeSandboxProvider(on_wake=slow))
    await client.post(f"/sessions/{session_id}/start")
    await _wait_for_mirrored_message(live_sessionmaker, session_id, "answer")

    old = app.state.session_runtimes[session_id]
    assert (await client.post(f"/sessions/{session_id}/pause")).status_code == 202
    await asyncio.wait_for(old.runner_task, timeout=5.0)  # the runner winds down once paused

    assert (await client.post(f"/sessions/{session_id}/resume")).status_code == 202
    await _wait_for_status(live_sessionmaker, session_id, "active")
    new = app.state.session_runtimes[session_id]

    assert new is not old
    assert old.mirror_task.done(), "the previous mirror must be stopped when the session is relaunched"
    assert not new.mirror_task.done()
    await _wait_for_status(live_sessionmaker, session_id, "finished", timeout=10.0)


async def test_a_finished_session_stops_its_mirror(client, live_sessionmaker, tmp_path):
    """The mirror used to poll forever after its session ended, one leaked task per finished session."""
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(a), "seat_order": i} for i, a in enumerate(agent_ids)],
    })
    session_id = uuid.UUID(resp.json()["session"]["id"])
    _wire_fakes(FakeSandboxProvider(on_wake=_auto_responder(tmp_path / "sessions", {"id": session_id})))
    await client.post(f"/sessions/{session_id}/start")
    await _wait_for_status(live_sessionmaker, session_id, "finished", timeout=10.0)
    runtime = app.state.session_runtimes[session_id]
    await asyncio.wait_for(asyncio.gather(runtime.mirror_task, return_exceptions=True), timeout=5.0)
    assert runtime.mirror_task.done()


# ---------------------------------------------------------------- GET /sessions/{id} (M3.1)

async def test_get_session_returns_404_for_an_unknown_session(client, live_sessionmaker):
    resp = await client.get(f"/sessions/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"]["code"] == "not_found"


async def test_get_session_reports_round_usage_and_turn_counts(client, live_sessionmaker, tmp_path):
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0]), "seat_order": 0},
                   {"agent_id": str(agent_ids[1]), "seat_order": 1}],
        "budget": {"tokens": 1000},
    })
    session_id = uuid.UUID(resp.json()["session"]["id"])
    box = {"id": session_id}

    sandbox_provider = FakeSandboxProvider(
        on_wake=_auto_responder(tmp_path / "sessions", box, meta={"tokens_in": 1, "tokens_out": 1, "cost": 0.0001}))
    _wire_fakes(sandbox_provider)

    await client.post(f"/sessions/{session_id}/start")
    await _wait_for_status(live_sessionmaker, session_id, "finished")

    async with live_sessionmaker() as db:
        turns = (await db.execute(select(Turn).where(Turn.session_id == session_id))).scalars().all()
    expected_round = max(t.round for t in turns)
    expected_counts = {"a": 0, "b": 0}
    for t in turns:
        role = next(r for r, aid in zip(("a", "b"), agent_ids) if t.speaker_id == aid)
        expected_counts[role] += 1

    resp = await client.get(f"/sessions/{session_id}")
    assert resp.status_code == 200
    body = resp.json()["session"]
    assert body["status"] == "finished"
    assert body["stop_reason"] == "max_rounds"  # the room can say why the session ended
    assert body["round"] == expected_round
    assert body["budget"] == {"max_tokens": 1000}
    # round-robin-test.yaml: max_rounds=2 -- opening (a, b) then one more grant (a) hits it,
    # 3 turns total, each self-reporting {tokens_in: 1, tokens_out: 1, cost: 0.0001}.
    assert body["usage"] == {"tokens_in": 3, "tokens_out": 3, "cost": 0.0003}
    by_participant = {tc["participant"]: tc["turns"] for tc in body["turn_counts"]}
    assert by_participant == {k: v for k, v in expected_counts.items() if v > 0}


async def test_get_session_round_and_usage_are_zero_before_any_turns(client, live_sessionmaker):
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0])}],
    })
    session_id = resp.json()["session"]["id"]

    resp = await client.get(f"/sessions/{session_id}")
    assert resp.status_code == 200
    body = resp.json()["session"]
    assert body["status"] == "created"
    assert body["round"] == 0
    assert body["usage"] == {"tokens_in": 0, "tokens_out": 0, "cost": 0.0}
    assert body["turn_counts"] == []
    assert body["budget"] is None


async def test_get_session_reports_why_a_failed_session_failed(client, live_sessionmaker):
    from orchestrator.api.runtime import session_failures
    from orchestrator.models import Session

    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0])}],
    })
    session_id = resp.json()["session"]["id"]
    async with live_sessionmaker() as db:
        row = await db.get(Session, uuid.UUID(session_id))
        row.status = "failed"
        await db.commit()
    session_failures[uuid.UUID(session_id)] = "RuntimeError: sbx env create failed"

    body = (await client.get(f"/sessions/{session_id}")).json()["session"]
    assert body["status"] == "failed"
    assert body["failure_reason"] == "RuntimeError: sbx env create failed"


async def test_get_session_reports_the_start_stage_while_starting(client, live_sessionmaker):
    from orchestrator.api.runtime import session_stages
    from orchestrator.models import Session

    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0])}],
    })
    session_id = resp.json()["session"]["id"]
    async with live_sessionmaker() as db:
        row = await db.get(Session, uuid.UUID(session_id))
        row.status = "starting"
        await db.commit()
    session_stages[uuid.UUID(session_id)] = "creating the sandbox (sbx env create)"

    body = (await client.get(f"/sessions/{session_id}")).json()["session"]
    assert body["stage"] == "creating the sandbox (sbx env create)"


# ---------------------------------------------------------------- GET /sessions/{id}/artifacts

async def test_get_artifacts_404_for_unknown_session(client, live_sessionmaker):
    resp = await client.get(f"/sessions/{uuid.uuid4()}/artifacts")
    assert resp.status_code == 404


async def test_get_artifacts_empty_then_returns_synthesis(client, live_sessionmaker):
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id), "roster": [{"agent_id": str(agent_ids[0])}],
    })
    session_id = uuid.UUID(resp.json()["session"]["id"])

    assert (await client.get(f"/sessions/{session_id}/artifacts")).json() == {"artifacts": []}

    async with live_sessionmaker() as db:
        db.add(Artifact(session_id=session_id, type="synthesis", content_json={"recommendation": "ship weekly"},
                        source_turn_ids=[]))
        await db.commit()
    body = (await client.get(f"/sessions/{session_id}/artifacts")).json()
    assert [a["type"] for a in body["artifacts"]] == ["synthesis"]
    assert body["artifacts"][0]["content_json"] == {"recommendation": "ship weekly"}


# ---------------------------------------------------------------- retiring a runner lets its turn finish

async def test_retire_lets_an_in_flight_turn_finish_before_cancelling():
    from orchestrator.api.runtime import SessionRuntime, _retire_runtime

    finished = []

    async def runner():
        await asyncio.sleep(0.2)  # the paused runner's last turn
        finished.append(True)

    async def forever():
        await asyncio.sleep(60)

    rt = SessionRuntime(conversation=None, pubsub=None, mirror_task=asyncio.create_task(forever()),
                        runner_task=asyncio.create_task(runner()))
    await _retire_runtime(rt, grace=5.0)
    assert finished == [True]  # not cancelled mid-turn
    assert rt.runner_task.done() and rt.mirror_task.cancelled()


async def test_retire_cancels_a_runner_that_does_not_stop_within_the_grace():
    from orchestrator.api.runtime import SessionRuntime, _retire_runtime

    async def forever():
        await asyncio.sleep(60)

    rt = SessionRuntime(conversation=None, pubsub=None, mirror_task=asyncio.create_task(forever()),
                        runner_task=asyncio.create_task(forever()))
    await asyncio.wait_for(_retire_runtime(rt, grace=0.2), timeout=5)
    assert rt.runner_task.cancelled() and rt.mirror_task.cancelled()


# ---------------------------------------------------------------- one running session per task

async def _two_sessions_on_one_task(client, live_sessionmaker):
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    ids = []
    for _ in range(2):
        resp = await client.post("/sessions", json={
            "task_id": str(task_id), "mode_id": str(mode_id), "roster": [{"agent_id": str(agent_ids[0])}]})
        ids.append(uuid.UUID(resp.json()["session"]["id"]))
    return ids


async def _set_status(sessionmaker, session_id, status):
    async with sessionmaker() as db:
        (await db.get(Session, session_id)).status = status
        await db.commit()


async def test_a_second_session_cannot_start_while_another_of_the_task_is_running(client, live_sessionmaker):
    first, second = await _two_sessions_on_one_task(client, live_sessionmaker)
    await _set_status(live_sessionmaker, first, "active")
    _wire_fakes(FakeSandboxProvider())
    resp = await client.post(f"/sessions/{second}/start")
    assert resp.status_code == 409
    err = resp.json()["detail"]["error"]
    assert err["code"] == "task_busy" and str(first)[:8] in err["detail"]
    async with live_sessionmaker() as db:
        assert (await db.get(Session, second)).status == "created"  # untouched, can be started later


async def test_a_paused_or_finished_session_does_not_block_a_new_one(client, live_sessionmaker):
    first, second = await _two_sessions_on_one_task(client, live_sessionmaker)
    _wire_fakes(FakeSandboxProvider())
    for status in ("needs_human", "finished"):
        await _set_status(live_sessionmaker, first, status)
        async with live_sessionmaker() as db:
            (await db.get(Session, second)).status = "created"
            await db.commit()
        resp = await client.post(f"/sessions/{second}/start")
        assert resp.status_code == 202, status
        await asyncio.sleep(0.05)
        await _set_status(live_sessionmaker, second, "failed")  # stop the background start between rounds


async def test_resuming_a_paused_session_is_refused_while_another_is_running(client, live_sessionmaker):
    first, second = await _two_sessions_on_one_task(client, live_sessionmaker)
    await _set_status(live_sessionmaker, first, "needs_human")
    await _set_status(live_sessionmaker, second, "active")
    _wire_fakes(FakeSandboxProvider())
    resp = await client.post(f"/sessions/{first}/resume")
    assert resp.status_code == 409 and resp.json()["detail"]["error"]["code"] == "task_busy"


# ---- archive, unarchive and delete

async def _created_session(client, live_sessionmaker):
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0]), "seat_order": 0}],
    })
    return task_id, uuid.UUID(resp.json()["session"]["id"])


async def test_archive_hides_a_session_from_the_task_board_and_unarchive_brings_it_back(client, live_sessionmaker):
    task_id, session_id = await _created_session(client, live_sessionmaker)

    def board_row(tasks):
        return next(t for t in tasks if t["id"] == str(task_id))

    assert board_row((await client.get("/tasks")).json())["last_session_id"] == str(session_id)

    assert (await client.post(f"/sessions/{session_id}/archive")).status_code == 200
    assert board_row((await client.get("/tasks")).json())["last_session_id"] is None
    detail = (await client.get(f"/sessions/{session_id}")).json()["session"]
    assert detail["archived_at"] is not None  # still there, just hidden

    assert (await client.post(f"/sessions/{session_id}/unarchive")).status_code == 200
    assert board_row((await client.get("/tasks")).json())["last_session_id"] == str(session_id)


async def test_delete_removes_the_session_and_what_belongs_to_it(client, live_sessionmaker, tmp_path):
    task_id, session_id = await _created_session(client, live_sessionmaker)
    folder = tmp_path / "sessions" / str(session_id)
    folder.mkdir(parents=True)
    (folder / "x.json").write_text("{}")

    resp = await client.delete(f"/sessions/{session_id}")
    assert resp.status_code == 204

    assert (await client.get(f"/sessions/{session_id}")).status_code == 404
    assert not folder.exists()
    async with live_sessionmaker() as db:
        assert await db.get(Session, session_id) is None
        assert await db.scalar(select(SessionAgent).where(SessionAgent.session_id == session_id)) is None
        assert await db.get(Task, task_id) is not None  # the task stays
    assert (await client.delete(f"/sessions/{session_id}")).status_code == 404


async def test_a_running_session_cannot_be_archived_or_deleted(client, live_sessionmaker):
    task_id, session_id = await _created_session(client, live_sessionmaker)
    async with live_sessionmaker() as db:
        (await db.get(Session, session_id)).status = "active"
        await db.commit()
    for call in (client.post(f"/sessions/{session_id}/archive"), client.delete(f"/sessions/{session_id}")):
        resp = await call
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"]["code"] == "session_busy"
    async with live_sessionmaker() as db:
        assert await db.get(Session, session_id) is not None


async def test_a_session_lifecycle_is_written_to_the_audit_trail(client, live_sessionmaker, tmp_path, span_exporter):
    from audit_helpers import audit_events
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(agent_ids[0]), "seat_order": 0}],
        "budget": {"dollars": 5.0},
    })
    session_id = uuid.UUID(resp.json()["session"]["id"])
    box = {"id": session_id}

    async def on_wake(role):
        await asyncio.sleep(0.2)
        await _auto_responder(tmp_path / "sessions", box)(role)

    _wire_fakes(FakeSandboxProvider(on_wake=on_wake))
    await client.post(f"/sessions/{session_id}/start")
    await _wait_for_status(live_sessionmaker, session_id, "active")
    assert (await client.post(f"/sessions/{session_id}/interject", json={"body": "PRIVATE-NOTE-TEXT"})).status_code == 200
    await client.post(f"/sessions/{session_id}/stop", json={"synthesize": False, "reason": "enough"})
    await _wait_for_status(live_sessionmaker, session_id, "finished", timeout=10.0)
    assert (await client.delete(f"/sessions/{session_id}")).status_code == 204

    rows = audit_events(span_exporter, session_id=session_id)
    assert [r.action for r in rows] == ["session.created", "session.started", "session.interjected",
                                        "session.stop_requested", "session.ended", "session.deleted"]
    created, started, interjected, stop, ended, deleted = rows
    assert created.detail["roster"][0]["name"] and created.detail["budget"] == {"max_cost": 5.0}
    [seat] = started.detail["seats"]
    assert {"Read", "Grep", "Glob"} <= set(seat["allowed"]) and {"Write", "Edit", "Bash", "WebFetch"} <= set(seat["denied"])
    assert started.actor == "system" and ended.actor == "system" and created.actor == "person"
    assert interjected.detail["chars"] == len("PRIVATE-NOTE-TEXT") and "PRIVATE-NOTE-TEXT" not in str(interjected.detail)
    assert stop.detail == {"synthesize": False, "reason": "enough"}
    assert ended.detail["stop_reason"] and ended.detail["synthesized"] is False


async def test_a_build_agent_cannot_join_a_discussion_session(client, live_sessionmaker):
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker, roles=("a", "build-developer"))
    resp = await client.post("/sessions", json={
        "task_id": str(task_id), "mode_id": str(mode_id),
        "roster": [{"agent_id": str(i), "seat_order": n} for n, i in enumerate(agent_ids)],
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"]["code"] == "invalid_agent"


async def test_a_task_is_deleted_with_its_idle_sessions(client, live_sessionmaker):
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    other = await client.post("/tasks", json={"title": "x", "brief": "b", "output_type": "decision"})
    assert other.status_code == 201, other.text
    other_id = other.json()["task"]["id"]
    assert (await client.delete(f"/tasks/{other_id}")).status_code == 204
    assert (await client.delete(f"/tasks/{other_id}")).status_code == 404
    await client.post("/sessions", json={"task_id": str(task_id), "mode_id": str(mode_id),
                                         "roster": [{"agent_id": str(agent_ids[0]), "seat_order": 0}]})
    # a task whose sessions are all idle is deleted together with them
    assert (await client.delete(f"/tasks/{task_id}")).status_code == 204
    assert (await client.get(f"/tasks/{task_id}/attachments")).status_code in (200, 404)
