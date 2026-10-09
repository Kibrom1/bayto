"""Session lifecycle endpoints (M2.8 + M3.3): create, start, interject, stop, pause, resume, SSE event stream.

Zero auth: no bearer tokens, no caller identity, no per-session ownership checks, no rate
limiting. Anyone who can reach this port can create sessions and interject/stop on any of
them. Do not expose this service beyond a private/internal network until M5 (human
seat/auth) lands. See docs/decisions.md and README.md.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sse_starlette.sse import EventSourceResponse

from ..models import Agent, Artifact, Message, Mode, Session, SessionAgent, Task, Turn
from ..moderator.budget import session_usage
from ..moderator.runner import STATUS_ACTIVE, STATUS_CANCELLING, STATUS_NEEDS_HUMAN, STATUS_STOPPING
from ..modes_registry import ModeNotFoundError, ModeParseError, resolve_mode
from .deps import get_db_sessionmaker, get_hand_raise_scorer_factory, get_sandbox_provider, get_summarizer, get_synthesizer
from .runtime import launch_runner, session_failures

log = logging.getLogger(__name__)

router = APIRouter()

STATUS_CREATED = "created"
STATUS_STARTING = "starting"


def _error(code: str, detail: str, field: str | None = None) -> dict:
    body: dict = {"code": code, "detail": detail}
    if field is not None:
        body["field"] = field
    return {"error": body}


# ---------------------------------------------------------------- POST /sessions

class BudgetIn(BaseModel):
    # M2.10: reject budget: {tokens: -5} or {tokens: 0} at creation time -- a non-positive
    # cap is nonsensical (it would either be silently accepted and only surface as a
    # confusing immediate-trip inside the stop-check, or a 0 would be indistinguishable
    # from "no cap" in a truthiness check). gt=0, not ge=0: a cap of exactly 0 isn't a
    # meaningful budget either.
    tokens: int | None = Field(default=None, gt=0)
    dollars: float | None = Field(default=None, gt=0)


class RosterEntry(BaseModel):
    agent_id: uuid.UUID
    seat_order: int | None = None
    muted: bool = False
    harness: str | None = None
    model: str | None = None
    isolation: Literal["shared", "own"] = "shared"


class SessionCreateRequest(BaseModel):
    task_id: uuid.UUID
    mode_id: uuid.UUID
    roster: list[RosterEntry]
    budget: BudgetIn | None = None


class SessionOut(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    mode_id: uuid.UUID
    status: str
    budget: dict | None
    started_at: datetime | None
    ended_at: datetime | None


class SessionAgentOut(BaseModel):
    session_id: uuid.UUID
    agent_id: uuid.UUID
    agent_version: str | None
    seat_order: int | None
    muted: bool
    harness: str | None
    model: str | None
    isolation: str
    removed_at: datetime | None  # M3.2


class SessionCreateResponse(BaseModel):
    session: SessionOut
    session_agents: list[SessionAgentOut]


def _budget_dict(budget: BudgetIn | None) -> dict | None:
    """Translates the wire request's {tokens, dollars} into the {max_tokens, max_cost}
    shape ModeratorRunner._budget_exceeded already assumes (M2.7, docs/decisions.md) --
    friendlier field names on the wire, the same internal shape in the DB."""
    if budget is None:
        return None
    out = {}
    if budget.tokens is not None:
        out["max_tokens"] = budget.tokens
    if budget.dollars is not None:
        out["max_cost"] = budget.dollars
    return out


@router.post("/sessions", status_code=201, response_model=SessionCreateResponse)
async def create_session(
    req: SessionCreateRequest,
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
) -> SessionCreateResponse:
    async with sessionmaker() as db:
        if await db.get(Task, req.task_id) is None:
            raise HTTPException(422, detail=_error("invalid_task", "task not found", "task_id"))

        mode_row = await db.get(Mode, req.mode_id)
        if mode_row is None:
            raise HTTPException(422, detail=_error("invalid_mode", "mode not found", "mode_id"))
        try:
            resolve_mode(mode_row.name)  # eager, at create time -- see modes_registry.resolve_mode
        except (ModeNotFoundError, ModeParseError) as exc:
            raise HTTPException(422, detail=_error("invalid_mode", str(exc), "mode_id"))

        session_row = Session(task_id=req.task_id, mode_id=req.mode_id, status=STATUS_CREATED,
                               budget=_budget_dict(req.budget))
        db.add(session_row)
        await db.flush()

        session_agents = []
        for entry in req.roster:
            agent_row = await db.get(Agent, entry.agent_id)
            if agent_row is None:
                raise HTTPException(422, detail=_error("invalid_agent", f"agent {entry.agent_id} not found",
                                                        "roster.agent_id"))
            sa = SessionAgent(session_id=session_row.id, agent_id=entry.agent_id,
                               agent_version=agent_row.version,  # snapshotted server-side, never client-supplied
                               seat_order=entry.seat_order, muted=entry.muted, harness=entry.harness,
                               model=entry.model, isolation=entry.isolation)
            db.add(sa)
            session_agents.append(sa)

        await db.commit()
        await db.refresh(session_row)
        return SessionCreateResponse(
            session=SessionOut.model_validate(session_row, from_attributes=True),
            session_agents=[SessionAgentOut.model_validate(sa, from_attributes=True) for sa in session_agents],
        )


# ---------------------------------------------------------------- POST /sessions/{id}/start

@router.post("/sessions/{session_id}/start", status_code=202)
async def start_session(
    session_id: uuid.UUID,
    request: Request,
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
    sandbox_provider=Depends(get_sandbox_provider),
    summarizer=Depends(get_summarizer),
    synthesizer=Depends(get_synthesizer),
    scorer_factory=Depends(get_hand_raise_scorer_factory),
) -> dict:
    async with sessionmaker() as db:
        row = await db.get(Session, session_id)
        if row is None:
            raise HTTPException(404, detail=_error("not_found", "session not found"))
        if row.status != STATUS_CREATED:
            raise HTTPException(409, detail=_error("invalid_transition",
                                                     f"cannot start a session in status {row.status!r}"))
        row.status = STATUS_STARTING
        await db.commit()

    # Fire-and-forget: the real work (sandbox create/start_team, building the runner,
    # flipping status to "active") happens in the background; this endpoint returns 202
    # immediately, same "don't block" posture as /stop.
    asyncio.create_task(launch_runner(
        session_id, sessionmaker=sessionmaker, sandbox_provider=sandbox_provider,
        summarizer=summarizer, synthesizer=synthesizer, scorer_factory=scorer_factory,
        running_sessions=request.app.state.running_sessions,
        session_runtimes=request.app.state.session_runtimes,
    ))
    return {"session": {"id": str(session_id), "status": STATUS_STARTING}}


# ---------------------------------------------------------------- POST /sessions/{id}/interject

class InterjectRequest(BaseModel):
    body: str
    to: list[str] | None = None


class InterjectResponse(BaseModel):
    envelope: dict


@router.post("/sessions/{session_id}/interject", response_model=InterjectResponse)
async def interject_session(session_id: uuid.UUID, req: InterjectRequest, request: Request) -> InterjectResponse:
    """Lands in the transcript like any other message -- no special transport-level
    handling. Never wakes anyone (only ModeratorRunner's grant flow does that), so this
    can never interrupt whoever currently holds the floor, same rule as Grant/wake-up.
    For raise-hand mode, a directed (non-broadcast) interject also becomes an
    addressed=ADDRESSED hand-raise the next time the runner collects hand-raises (see
    ModeratorRunner._interjected_addressed_hand_raises); round-robin's rotation is
    unaffected either way, since next_seat() never reads message content."""
    runtime = request.app.state.session_runtimes.get(session_id)
    if runtime is None:
        raise HTTPException(409, detail=_error("not_started", "session is not running"))
    to = req.to or ["*"]
    env = await runtime.conversation.send(sender="human", to=to, kind="note", body=req.body)
    return InterjectResponse(envelope=env.dump())


# ---------------------------------------------------------------- POST /sessions/{id}/stop

class StopRequest(BaseModel):
    synthesize: bool = True
    reason: str | None = None


@router.post("/sessions/{session_id}/stop", status_code=202)
async def stop_session(
    session_id: uuid.UUID,
    req: StopRequest,
    request: Request,
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
) -> dict:
    async with sessionmaker() as db:
        row = await db.get(Session, session_id)
        if row is None:
            raise HTTPException(404, detail=_error("not_found", "session not found"))
        if row.status not in (STATUS_ACTIVE, STATUS_NEEDS_HUMAN):
            raise HTTPException(409, detail=_error("invalid_transition",
                                                     f"cannot stop a session in status {row.status!r}"))
        row.status = STATUS_STOPPING if req.synthesize else STATUS_CANCELLING
        await db.commit()
        new_status = row.status

    # M2.9: `synthesize` is already durable via the status value itself (STOPPING vs
    # CANCELLING), so a crash can't lose that -- but `reason` was accepted and never
    # persisted anywhere. Record both as a durable message, reusing the same mechanism
    # interject already uses, rather than adding a Session column; an "x-stop" extension
    # kind since the closed Kind vocabulary has no "stop" member.
    runtime = request.app.state.session_runtimes.get(session_id)
    if runtime is not None:
        await runtime.conversation.send(sender="human", to=["*"], kind="x-stop",
                                         body=json.dumps({"synthesize": req.synthesize, "reason": req.reason}))
    else:
        log.warning("stop requested for session %s with no live runtime to record a durable "
                    "x-stop message (status still updated)", session_id)

    # Fire-and-forget: ModeratorRunner notices the status change on its next iteration
    # (worst case the 300s turn-wait timeout) and winds down; this endpoint does not block
    # until that completes.
    return {"session": {"id": str(session_id), "status": new_status}}


# ---------------------------------------------------------------- M3.3: pause / resume

@router.post("/sessions/{session_id}/pause", status_code=202)
async def pause_session(
    session_id: uuid.UUID,
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
) -> dict:
    """Freeze turn-granting until a matching /resume. Sets status to `needs_human`,
    which ModeratorRunner.run_once() already checks at the top of every iteration and
    returns early without granting anything when it sees it (M2.7, runner.py line 182+).

    Guard: only `active` sessions can be paused. `needs_human` itself is idempotent
    (pausing an already-paused session is 409) -- `stopping`/`cancelling`/`finished`/
    `failed`/`orphaned` are all terminal or mid-wind-down and must not be re-paused."""
    async with sessionmaker() as db:
        row = await db.get(Session, session_id)
        if row is None:
            raise HTTPException(404, detail=_error("not_found", "session not found"))
        if row.status != STATUS_ACTIVE:
            raise HTTPException(409, detail=_error(
                "invalid_transition",
                f"can only pause an active session (current status: {row.status!r})"))
        row.status = STATUS_NEEDS_HUMAN
        await db.commit()
    return {"session": {"id": str(session_id), "status": STATUS_NEEDS_HUMAN}}


@router.post("/sessions/{session_id}/resume", status_code=202)
async def resume_session(
    session_id: uuid.UUID,
    request: Request,
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
    sandbox_provider=Depends(get_sandbox_provider),
    summarizer=Depends(get_summarizer),
    synthesizer=Depends(get_synthesizer),
    scorer_factory=Depends(get_hand_raise_scorer_factory),
) -> dict:
    """Resume a paused (`needs_human`) session by relaunching the ModeratorRunner via
    the same `launch_runner()` path M2.9's restart-reconcile already uses (reconcile.py's
    `RESUMABLE_STATUSES` deliberately excludes `needs_human` so the orchestrator never
    auto-resumes on restart -- that exclusion still holds; this endpoint is the *explicit*
    human action that un-pauses). The runner is re-launched against the existing sandbox
    and conversation; `launch_runner()` sets status back to `active` before starting the
    loop. M2.9's `_pending_grant()` check at the top of every run_once() call covers the
    resume case transparently -- no resume-mode flag needed inside ModeratorRunner.

    Guard: only `needs_human` sessions can be resumed. A fresh `launch_runner()` for an
    already-active session (e.g. a double-tap of /resume) would create a second parallel
    runner, which is wrong -- the 409 prevents that."""
    async with sessionmaker() as db:
        row = await db.get(Session, session_id)
        if row is None:
            raise HTTPException(404, detail=_error("not_found", "session not found"))
        if row.status != STATUS_NEEDS_HUMAN:
            raise HTTPException(409, detail=_error(
                "invalid_transition",
                f"can only resume a paused (needs_human) session (current status: {row.status!r})"))
        # Flip status back to `starting` so launch_runner() follows its normal path:
        # it reads `starting`, creates the sandbox if needed, then flips to `active`.
        # This also prevents a double-tap of /resume from racing into two parallel runners
        # (the second would see `starting`, not `needs_human`, and 409 immediately).
        row.status = STATUS_STARTING
        await db.commit()

    asyncio.create_task(launch_runner(
        session_id, sessionmaker=sessionmaker, sandbox_provider=sandbox_provider,
        summarizer=summarizer, synthesizer=synthesizer, scorer_factory=scorer_factory,
        running_sessions=request.app.state.running_sessions,
        session_runtimes=request.app.state.session_runtimes,
    ))
    return {"session": {"id": str(session_id), "status": STATUS_STARTING}}


# ---------------------------------------------------------------- participant lifecycle (M3.2)

# A session must be genuinely running for a participant-lifecycle change to mean anything
# to the live ModeratorRunner -- not yet started (created/starting) or already wound down
# (stopping/cancelling/finished/failed/orphaned). The architect's spec only wrote this guard
# out explicitly for add; applied uniformly to mute/unmute/remove too for the same reason
# (see docs/decisions.md).
_LIVE_SESSION_STATUSES = (STATUS_ACTIVE, STATUS_NEEDS_HUMAN)


class ParticipantAddRequest(BaseModel):
    agent_id: uuid.UUID
    seat_order: int | None = None


class ParticipantResponse(BaseModel):
    session_agent: SessionAgentOut


async def _require_live_session(db, session_id: uuid.UUID) -> Session:
    row = await db.get(Session, session_id)
    if row is None:
        raise HTTPException(404, detail=_error("not_found", f"session {session_id} not found", "id"))
    if row.status not in _LIVE_SESSION_STATUSES:
        raise HTTPException(409, detail=_error(
            "session_not_active", f"session is in status {row.status!r}, not a live session", "id"))
    return row


async def _get_participant(db, session_id: uuid.UUID, agent_id: uuid.UUID) -> SessionAgent:
    row = await db.get(SessionAgent, (session_id, agent_id))
    if row is None:
        raise HTTPException(404, detail=_error(
            "not_found", f"agent {agent_id} is not a participant in session {session_id}", "agent_id"))
    return row


@router.post("/sessions/{session_id}/participants/{agent_id}/mute", response_model=ParticipantResponse)
async def mute_participant(
    session_id: uuid.UUID, agent_id: uuid.UUID,
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
) -> ParticipantResponse:
    async with sessionmaker() as db:
        await _require_live_session(db, session_id)
        sa = await _get_participant(db, session_id, agent_id)
        sa.muted = True
        await db.commit()
        await db.refresh(sa)
        return ParticipantResponse(session_agent=SessionAgentOut.model_validate(sa, from_attributes=True))


@router.post("/sessions/{session_id}/participants/{agent_id}/unmute", response_model=ParticipantResponse)
async def unmute_participant(
    session_id: uuid.UUID, agent_id: uuid.UUID,
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
) -> ParticipantResponse:
    async with sessionmaker() as db:
        await _require_live_session(db, session_id)
        sa = await _get_participant(db, session_id, agent_id)
        sa.muted = False
        await db.commit()
        await db.refresh(sa)
        return ParticipantResponse(session_agent=SessionAgentOut.model_validate(sa, from_attributes=True))


@router.post("/sessions/{session_id}/participants/{agent_id}/remove", response_model=ParticipantResponse)
async def remove_participant(
    session_id: uuid.UUID, agent_id: uuid.UUID,
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
) -> ParticipantResponse:
    """Permanent (no un-remove endpoint built, product-owner-confirmed for v1) -- a
    nullable `removed_at` rather than a hard delete: a hard delete would lose the
    participation record the roster panel needs and isn't replay-safe (M2.9's resilience
    story depends on recomputing from persisted rows). A prior Turn row from this
    participant stays fully visible -- Turn rows are immutable/append-only throughout this
    system, removal never touches them. Idempotent: removing an already-removed participant
    leaves the original removed_at untouched rather than bumping it -- "when were they
    removed" is a stable historical fact, not something a retried request should
    overwrite."""
    async with sessionmaker() as db:
        await _require_live_session(db, session_id)
        sa = await _get_participant(db, session_id, agent_id)
        if sa.removed_at is None:
            sa.removed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(sa)
        return ParticipantResponse(session_agent=SessionAgentOut.model_validate(sa, from_attributes=True))


@router.post("/sessions/{session_id}/participants", status_code=201, response_model=ParticipantResponse)
async def add_participant(
    session_id: uuid.UUID, req: ParticipantAddRequest,
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
) -> ParticipantResponse:
    """Any Agent row is allowed -- "templates only" is a UI curation concern (M3.9's picker
    can choose to only offer GET /agents/templates results), not a backend constraint.
    Re-adding a previously-removed participant (the SessionAgent row still exists with
    removed_at set) is 409 already_participant, same as adding someone already present --
    product-owner-confirmed: explicit beats implicit, a client who removed someone
    deliberately should never get a surprising silent un-remove from a stale/retried add
    request."""
    async with sessionmaker() as db:
        await _require_live_session(db, session_id)

        if await db.get(SessionAgent, (session_id, req.agent_id)) is not None:
            raise HTTPException(409, detail=_error(
                "already_participant", f"agent {req.agent_id} is already a participant", "agent_id"))

        agent_row = await db.get(Agent, req.agent_id)
        if agent_row is None:
            raise HTTPException(404, detail=_error("not_found", f"agent {req.agent_id} not found", "agent_id"))

        seat_order = req.seat_order
        if seat_order is None:
            # Append to the end of the existing rotation.
            max_seat = await db.scalar(
                select(func.max(SessionAgent.seat_order)).where(SessionAgent.session_id == session_id)
            )
            seat_order = (max_seat + 1) if max_seat is not None else 0

        sa = SessionAgent(session_id=session_id, agent_id=req.agent_id, agent_version=agent_row.version,
                           seat_order=seat_order)
        db.add(sa)
        await db.commit()
        await db.refresh(sa)
        return ParticipantResponse(session_agent=SessionAgentOut.model_validate(sa, from_attributes=True))


# ---------------------------------------------------------------- GET /sessions/{id}

class UsageOut(BaseModel):
    tokens_in: int
    tokens_out: int
    cost: float


class TurnCountOut(BaseModel):
    participant: str | None  # Agent.role is nullable in the schema; always set for a
    # seated participant in practice (ModeratorRunner's wire protocol identifies
    # participants by role -- see runner.py's module docstring), not enforced here.
    agent_id: uuid.UUID
    turns: int


class SessionDetailOut(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    mode_id: uuid.UUID
    status: str
    started_at: datetime | None
    ended_at: datetime | None
    round: int
    budget: dict | None
    usage: UsageOut
    turn_counts: list[TurnCountOut]
    failure_reason: str | None = None


class SessionDetailResponse(BaseModel):
    session: SessionDetailOut


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session(
    session_id: uuid.UUID,
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
) -> SessionDetailResponse:
    async with sessionmaker() as db:
        row = await db.get(Session, session_id)
        if row is None:
            raise HTTPException(404, detail=_error("not_found", f"session {session_id} not found", "id"))

        # Both floor policies already assign Turn.round per their own semantics at write
        # time (M2.7) -- this just surfaces whatever's already persisted, no new definition.
        max_round = await db.scalar(select(func.max(Turn.round)).where(Turn.session_id == session_id))

        turn_counts_rows = (await db.execute(
            select(Agent.role, Agent.id, func.count(Turn.id))
            .select_from(Turn)
            .join(Agent, Agent.id == Turn.speaker_id)
            .where(Turn.session_id == session_id)
            .group_by(Agent.id, Agent.role)
        )).all()

    usage = await session_usage(sessionmaker, session_id)
    return SessionDetailResponse(session=SessionDetailOut(
        id=row.id, task_id=row.task_id, mode_id=row.mode_id, status=row.status,
        started_at=row.started_at, ended_at=row.ended_at, round=max_round or 0, budget=row.budget,
        usage=UsageOut(tokens_in=usage.tokens_in, tokens_out=usage.tokens_out, cost=usage.cost),
        turn_counts=[TurnCountOut(participant=role, agent_id=agent_id, turns=count)
                     for role, agent_id, count in turn_counts_rows],
        failure_reason=session_failures.get(session_id) if row.status == "failed" else None,
    ))


# ---------------------------------------------------------------- GET /sessions/{id}/artifacts

class ArtifactOut(BaseModel):
    id: uuid.UUID
    type: str  # "synthesis" | "minority_report"
    content_json: dict
    source_turn_ids: list[uuid.UUID]


class ArtifactsResponse(BaseModel):
    artifacts: list[ArtifactOut]


@router.get("/sessions/{session_id}/artifacts", response_model=ArtifactsResponse)
async def get_session_artifacts(
    session_id: uuid.UUID,
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
) -> ArtifactsResponse:
    """The synthesis (and minority report, if any) the moderator wrote when the session
    stopped. Empty until then. `content_json` is free-form: its shape is chosen by the
    synthesizer, so clients should render it generically."""
    async with sessionmaker() as db:
        if await db.get(Session, session_id) is None:
            raise HTTPException(404, detail=_error("not_found", f"session {session_id} not found", "id"))
        rows = (await db.execute(
            select(Artifact).where(Artifact.session_id == session_id).order_by(Artifact.type.desc(), Artifact.id)
        )).scalars().all()
    return ArtifactsResponse(artifacts=[
        ArtifactOut(id=r.id, type=r.type, content_json=r.content_json, source_turn_ids=r.source_turn_ids)
        for r in rows
    ])


# ---------------------------------------------------------------- GET /sessions/{id}/events (SSE)

def _message_row_to_envelope_dict(row: Message) -> dict:
    """Same shape as Envelope.dump() (by_alias=True) -- the SSE backlog replay and the
    live pubsub tail must format identically, since a client may reconnect mid-stream."""
    return {
        "protocol": row.protocol, "message_id": row.message_id, "conversation_id": row.conversation_id,
        "attempt": row.attempt, "seq": row.seq, "from": row.from_, "to": row.to, "visibility": row.visibility,
        "kind": row.kind, "in_reply_to": row.in_reply_to, "thread_id": row.thread_id,
        "requires_ack": row.requires_ack, "refs": row.refs, "body": row.body, "body_format": row.body_format,
        "meta": row.meta, "created_at": row.created_at.isoformat(),
    }


@router.get("/sessions/{session_id}/events")
async def session_events(
    session_id: uuid.UUID,
    request: Request,
    since_seq: int | None = None,
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
) -> EventSourceResponse:
    """Message events only, per the work-plan line -- floor-decision/status-change events
    are NOT added to this channel, even though they'd be easy to add later."""

    async def event_stream():
        if since_seq is not None:
            async with sessionmaker() as db:
                rows = (await db.execute(
                    select(Message).where(Message.session_id == session_id, Message.seq > since_seq)
                    .order_by(Message.seq)
                )).scalars().all()
            for row in rows:
                if await request.is_disconnected():
                    return
                yield json.dumps(_message_row_to_envelope_dict(row))

        runtime = request.app.state.session_runtimes.get(session_id)
        if runtime is None or runtime.runner_task.done():
            return  # nothing live to tail: never started, or already finished/failed

        queue = runtime.pubsub.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if event.get("type") == "message":
                    yield json.dumps(event["data"])
                elif event.get("type") in ("rolling_summary", "turn_delta", "turn_tool", "turn_done", "turn_error"):
                    yield {
                        "event": event["type"],
                        "data": json.dumps(event["data"])
                    }
        finally:
            runtime.pubsub.unsubscribe(queue)

    return EventSourceResponse(event_stream())
