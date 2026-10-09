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
    del app.state.session_runtimes[session_id]  # simulate the no-live-runtime edge case

    import logging
    with caplog.at_level(logging.WARNING):
        resp = await client.post(f"/sessions/{session_id}/stop", json={"synthesize": True})
    assert resp.status_code == 202
    assert resp.json()["session"]["status"] == "stopping"
    assert any("no live runtime" in r.message for r in caplog.records)

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

    # Connect to the live stream right away, no since_seq.
    async def _read_live():
        events = []
        async with client.stream("GET", f"/sessions/{session_id}/events") as resp:
            async for line in resp.aiter_lines():
                if line.startswith("event: rolling_summary"):
                    # the next line should be data
                    data_line = await resp.aiter_lines().__anext__()
                    events.append(json.loads(data_line[len("data:"):].strip()))
                    break
        return events

    # Wait for the session to finish in the background, but also listen to the stream.
    # The read will return as soon as it sees a rolling_summary event.
    events = await asyncio.wait_for(_read_live(), timeout=5.0)
    
    assert len(events) == 1
    assert events[0]["summary"] == "s"

    await _wait_for_status(live_sessionmaker, session_id, "finished")


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
    assert body == b""


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
