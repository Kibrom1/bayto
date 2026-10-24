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
import shutil
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sse_starlette.sse import EventSourceResponse

from .. import audit
from ..models import Agent, Artifact, Message, Mode, Report, Session, SessionAgent, Task, Turn, UsageLedger
from ..moderator.budget import session_usage
from ..moderator.runner import STATUS_ACTIVE, STATUS_CANCELLING, STATUS_NEEDS_HUMAN, STATUS_STOPPING
from ..models_catalog import seat_model_problem
from ..providers import provider_id_of
from ..modes_registry import ModeNotFoundError, ModeParseError, resolve_mode
from .deps import get_db_sessionmaker, get_login_check, get_hand_raise_scorer_factory, get_sandbox_provider, get_summarizer, get_synthesizer
from .runtime import _retire_runtime, factory_root, launch_runner, session_failures, session_stages
from .types import LABEL_MAX, MESSAGE_MAX, REASON_MAX, text

log = logging.getLogger(__name__)

router = APIRouter()
_launch_tasks: set[asyncio.Task] = set()

STATUS_CREATED = "created"
STATUS_STARTING = "starting"
MAX_ROSTER = 12  # a panel, not a crowd: every seat costs a turn and a share of the budget


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
    harness: text(LABEL_MAX) | None = None
    model: text(LABEL_MAX) | None = None
    isolation: Literal["shared", "own"] = "shared"


class SessionCreateRequest(BaseModel):
    task_id: uuid.UUID
    mode_id: uuid.UUID
    roster: list[RosterEntry] = Field(min_length=1, max_length=MAX_ROSTER)
    budget: BudgetIn | None = None
    synthesis_model: text(LABEL_MAX) | None = None  # the model that writes the final synthesis; null = the app default

    @model_validator(mode="after")
    def _roster_is_unique(self) -> "SessionCreateRequest":
        # A repeated agent hit the session_agent primary key (a 500); a repeated seat made
        # the speaking order ambiguous. Both are caller mistakes, so reject them up front.
        agent_ids = [e.agent_id for e in self.roster]
        if len(set(agent_ids)) != len(agent_ids):
            raise ValueError("roster lists the same agent more than once")
        seats = [e.seat_order for e in self.roster if e.seat_order is not None]
        if len(set(seats)) != len(seats):
            raise ValueError("roster has two agents with the same seat_order")
        return self


class SessionOut(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    mode_id: uuid.UUID
    status: str
    budget: dict | None
    started_at: datetime | None
    ended_at: datetime | None
    synthesis_model: str | None = None


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
        problem = seat_model_problem(req.synthesis_model, synthesis=True)
        if problem:
            raise HTTPException(422, detail=_error(problem[0], problem[1], "synthesis_model"))
        if mode_row.name == "build":
            task_row = await db.get(Task, req.task_id)
            if not (task_row.workspace or {}).get("test_command"):
                raise HTTPException(422, detail=_error(
                    "test_command_required",
                    "A Build session needs a test command: without one the developer and tester are not allowed "
                    "to run any tests. Set it in the task's workspace settings.", "workspace"))
        try:
            resolve_mode(mode_row.name)  # eager, at create time -- see modes_registry.resolve_mode
        except (ModeNotFoundError, ModeParseError) as exc:
            raise HTTPException(422, detail=_error("invalid_mode", str(exc), "mode_id"))

        session_row = Session(task_id=req.task_id, mode_id=req.mode_id, status=STATUS_CREATED,
                               budget=_budget_dict(req.budget), synthesis_model=req.synthesis_model)
        db.add(session_row)
        await db.flush()

        session_agents = []
        roster_log = []
        for entry in req.roster:
            agent_row = await db.get(Agent, entry.agent_id)
            if agent_row is None or agent_row.archived_at is not None:
                raise HTTPException(422, detail=_error("invalid_agent", f"agent {entry.agent_id} not found",
                                                        "roster.agent_id"))
            if (mode_row.name != "build" and (agent_row.role or "").startswith("build-")):
                raise HTTPException(422, detail=_error("invalid_agent", f"{agent_row.name} only works in a Build session",
                                                        "roster.agent_id"))
            problem = seat_model_problem(entry.model, build=mode_row.name == "build")
            if not problem and not entry.model and provider_id_of(agent_row.model) != "anthropic":
                problem = seat_model_problem(agent_row.model, build=mode_row.name == "build")  # the agent's own default
            if problem:
                raise HTTPException(422, detail=_error(problem[0], problem[1], "roster.model"))
            sa = SessionAgent(session_id=session_row.id, agent_id=entry.agent_id,
                               agent_version=agent_row.version,  # snapshotted server-side, never client-supplied
                               seat_order=entry.seat_order, muted=entry.muted, harness=entry.harness,
                               model=entry.model, isolation=entry.isolation)
            db.add(sa)
            session_agents.append(sa)
            roster_log.append({"agent_id": str(entry.agent_id), "name": agent_row.name, "role": agent_row.role,
                               "version": agent_row.version, "kind": agent_row.kind,
                               "model": sa.model or agent_row.model})

        if mode_row.name == "build":
            from ..build_policy import BUILD_ROLES
            missing = sorted(BUILD_ROLES - {r["role"] for r in roster_log})
            if missing:  # the setup page already blocks this; a direct API client must not get a pipeline that stalls
                raise HTTPException(422, detail=_error(
                    "build_roles_required",
                    "Build needs all four roles; missing: " + ", ".join(m.replace("build-", "") for m in missing),
                    "roster"))

        audit.emit(action="session.created", summary=f"Created a {mode_row.name} session with {len(roster_log)} agents",
                           task_id=req.task_id, session_id=session_row.id, subject_type="session", subject_id=session_row.id,
                           detail={"mode": mode_row.name, "budget": session_row.budget, "roster": roster_log,
                                   "synthesis_model": session_row.synthesis_model})
        await db.commit()
        await db.refresh(session_row)
        return SessionCreateResponse(
            session=SessionOut.model_validate(session_row, from_attributes=True),
            session_agents=[SessionAgentOut.model_validate(sa, from_attributes=True) for sa in session_agents],
        )


@router.post("/sessions/{session_id}/continue", status_code=201, response_model=SessionCreateResponse)
async def continue_session(session_id: uuid.UUID,
                           sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> SessionCreateResponse:
    """A new session with the same mode, team and budget, for a discussion that was interrupted (the app restarted)
    or failed. The old session's summary reaches the new one as earlier context on the task. Build sessions are
    refused: their sandbox and branch are not something to pick up blind."""
    async with sessionmaker() as db:
        old = await db.get(Session, session_id)
        if old is None:
            raise HTTPException(404, detail=_error("not_found", "session not found"))
        if old.status not in ("orphaned", "failed"):
            raise HTTPException(409, detail=_error("invalid_transition",
                                                    f"only an interrupted or failed session can be continued (this one is {old.status})"))
        mode_row = await db.get(Mode, old.mode_id)
        if mode_row is not None and mode_row.name == "build":
            raise HTTPException(409, detail=_error("build_not_continuable",
                                                    "a Build session can't be continued; start a new one from the task"))
        await _refuse_if_task_busy(db, old)
        seats = (await db.execute(select(SessionAgent).where(SessionAgent.session_id == session_id,
                                                              SessionAgent.removed_at.is_(None))
                                  .order_by(SessionAgent.seat_order))).scalars().all()
        new = Session(task_id=old.task_id, mode_id=old.mode_id, status=STATUS_CREATED, budget=old.budget,
                      synthesis_model=old.synthesis_model)
        db.add(new)
        await db.flush()
        out = []
        for sa in seats:
            agent_row = await db.get(Agent, sa.agent_id)
            if agent_row is None or agent_row.archived_at is not None:
                continue  # an agent archived since then is left out
            copy = SessionAgent(session_id=new.id, agent_id=sa.agent_id, agent_version=agent_row.version,
                                seat_order=len(out), muted=sa.muted, harness=sa.harness, model=sa.model,
                                isolation=sa.isolation)
            db.add(copy)
            out.append(copy)
        if len(out) < 2:
            raise HTTPException(422, detail=_error("roster_too_small", "fewer than two of its agents are still available"))
        audit.emit(action="session.continued", summary=f"Continued an interrupted session with {len(out)} agents",
                   task_id=old.task_id, session_id=new.id, subject_type="session", subject_id=new.id,
                   detail={"from_session": str(session_id), "from_status": old.status})
        await db.commit()
        await db.refresh(new)
        return SessionCreateResponse(
            session=SessionOut.model_validate(new, from_attributes=True),
            session_agents=[SessionAgentOut.model_validate(sa, from_attributes=True) for sa in out],
        )


# Sessions of one task share a sandbox and, in Build mode, one repository whose checked-out branch the
# session works on. A second running session would switch that branch under the first, so only one session
# of a task may be running at a time. A paused (`needs_human`) session does not count: resuming it puts the
# repository back on its own branch.
BUSY_STATUSES = ("starting", "active", "stopping", "cancelling")


async def _refuse_if_task_busy(db, row) -> None:
    other = await db.scalar(
        select(Session).where(Session.task_id == row.task_id, Session.id != row.id,
                              Session.status.in_(BUSY_STATUSES)).limit(1))
    if other is not None:
        raise HTTPException(409, detail=_error(
            "task_busy",
            f"Another session of this task is already {other.status} ({str(other.id)[:8]}). Wait for it to "
            "finish, or end it first: sessions of one task share a sandbox and a repository."))


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
    login_check=Depends(get_login_check),
) -> dict:
    await login_check()
    async with sessionmaker() as db:
        row = await db.get(Session, session_id)
        if row is None:
            raise HTTPException(404, detail=_error("not_found", "session not found"))
        if row.status != STATUS_CREATED:
            raise HTTPException(409, detail=_error("invalid_transition",
                                                     f"cannot start a session in status {row.status!r}"))
        await _refuse_if_task_busy(db, row)
        row.status = STATUS_STARTING
        await db.commit()

    # Fire-and-forget: the real work (sandbox create/start_team, building the runner,
    # flipping status to "active") happens in the background; this endpoint returns 202
    # immediately, same "don't block" posture as /stop.
    task = asyncio.create_task(launch_runner(
        session_id, sessionmaker=sessionmaker, sandbox_provider=sandbox_provider,
        summarizer=summarizer, synthesizer=synthesizer, scorer_factory=scorer_factory,
        running_sessions=request.app.state.running_sessions,
        session_runtimes=request.app.state.session_runtimes,
    ))
    # Keep a strong reference: the event loop only holds weak refs to tasks.
    _launch_tasks.add(task)
    task.add_done_callback(_launch_tasks.discard)
    return {"session": {"id": str(session_id), "status": STATUS_STARTING}}


# ---------------------------------------------------------------- POST /sessions/{id}/interject

class InterjectRequest(BaseModel):
    body: text(MESSAGE_MAX)
    to: list[text(LABEL_MAX)] | None = Field(default=None, max_length=MAX_ROSTER)


class InterjectResponse(BaseModel):
    envelope: dict


@router.post("/sessions/{session_id}/interject", response_model=InterjectResponse)
async def interject_session(session_id: uuid.UUID, req: InterjectRequest, request: Request,
                            sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> InterjectResponse:
    """Lands in the transcript like any other message. The text also goes into each agent's
    next prompt (ModeratorRunner._human_notes_for), once per agent, marked as the human's. Never wakes anyone (only ModeratorRunner's grant flow does that), so this
    can never interrupt whoever currently holds the floor, same rule as Grant/wake-up.
    For raise-hand mode, a directed (non-broadcast) interject also becomes an
    addressed=ADDRESSED hand-raise the next time the runner collects hand-raises (see
    ModeratorRunner._interjected_addressed_hand_raises); round-robin's rotation is
    unaffected either way, since next_seat() never reads message content."""
    runtime = request.app.state.session_runtimes.get(session_id)
    if runtime is None:
        raise HTTPException(409, detail=_error("not_started", "session is not running"))
    to = req.to or ["*"]
    if to != ["*"]:
        async with sessionmaker() as db:
            rows = (await db.execute(select(Agent.role, Agent.name).join(SessionAgent, SessionAgent.agent_id == Agent.id)
                                      .where(SessionAgent.session_id == session_id))).all()
        known = {v.lower() for r in rows for v in r if v}
        bad = [t for t in to if t.lower() not in known and t.lower() != "human"
               and not any(t.lower().startswith(k + "-") for k in known)]
        if bad:
            raise HTTPException(422, detail=_error("unknown_recipient", f"no participant named {', '.join(bad)}", "to"))
    env = await runtime.conversation.send(sender="human", to=to, kind="note", body=req.body)
    async with sessionmaker() as db:  # the text is in the transcript; the log keeps who it went to and how long it was
        session_row = await db.get(Session, session_id)
        audit.emit(action="session.interjected",
                           summary="Sent a message to " + ("everyone" if to == ["*"] else ", ".join(to)),
                           task_id=session_row.task_id if session_row else None, session_id=session_id,
                           subject_type="session", subject_id=session_id, detail={"to": to, "chars": len(req.body)})
        await db.commit()
    return InterjectResponse(envelope=env.dump())


# ---------------------------------------------------------------- POST /sessions/{id}/stop

class StopRequest(BaseModel):
    synthesize: bool = True
    reason: text(REASON_MAX) | None = None


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
        audit.emit(action="session.stop_requested",
                           summary="Ended the session" + (" with a synthesis" if req.synthesize else " without a synthesis"),
                           task_id=row.task_id, session_id=session_id, subject_type="session", subject_id=session_id,
                           detail={"synthesize": req.synthesize, "reason": req.reason})
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
        audit.emit(action="session.paused", summary="Paused the session", task_id=row.task_id,
                           session_id=session_id, subject_type="session", subject_id=session_id)
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
    login_check=Depends(get_login_check),
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
    await login_check()
    async with sessionmaker() as db:
        row = await db.get(Session, session_id)
        if row is None:
            raise HTTPException(404, detail=_error("not_found", "session not found"))
        if row.status != STATUS_NEEDS_HUMAN:
            raise HTTPException(409, detail=_error(
                "invalid_transition",
                f"can only resume a paused (needs_human) session (current status: {row.status!r})"))
        await _refuse_if_task_busy(db, row)
        # Flip status back to `starting` so launch_runner() follows its normal path:
        # it reads `starting`, creates the sandbox if needed, then flips to `active`.
        # This also prevents a double-tap of /resume from racing into two parallel runners
        # (the second would see `starting`, not `needs_human`, and 409 immediately).
        row.status = STATUS_STARTING
        audit.emit(action="session.resumed", summary="Resumed the session", task_id=row.task_id,
                           session_id=session_id, subject_type="session", subject_id=session_id)
        await db.commit()

    task = asyncio.create_task(launch_runner(
        session_id, sessionmaker=sessionmaker, sandbox_provider=sandbox_provider,
        summarizer=summarizer, synthesizer=synthesizer, scorer_factory=scorer_factory,
        running_sessions=request.app.state.running_sessions,
        session_runtimes=request.app.state.session_runtimes,
    ))
    # Keep a strong reference: the event loop only holds weak refs to tasks.
    _launch_tasks.add(task)
    task.add_done_callback(_launch_tasks.discard)
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


async def _record_participant(db, action: str, sa: SessionAgent, verb: str) -> None:
    agent = await db.get(Agent, sa.agent_id)
    session_row = await db.get(Session, sa.session_id)
    audit.emit(action=action, summary=f"{verb} {agent.name if agent else sa.agent_id}",
                       task_id=session_row.task_id if session_row else None, session_id=sa.session_id,
                       subject_type="agent", subject_id=sa.agent_id,
                       detail={"name": agent.name if agent else None, "role": agent.role if agent else None})


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
        await _record_participant(db, "participant.muted", sa, "Muted")
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
        await _record_participant(db, "participant.unmuted", sa, "Unmuted")
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
            await _record_participant(db, "participant.removed", sa, "Removed")
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
        if agent_row is None or agent_row.archived_at is not None:
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
        await db.flush()
        await _record_participant(db, "participant.added", sa, "Added")
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
    model: str | None = None  # what this seat runs on: its own choice for the session, else the agent's default
    muted: bool = False  # the seat stays in the room but is not given the floor


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
    stage: str | None = None
    # Why a finished session ended (max_rounds, no_new_arguments, user_stop, ...); null while it runs.
    stop_reason: str | None = None
    archived_at: datetime | None = None


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

        seat_models = {aid: sa_model or agent_model for aid, sa_model, agent_model in (await db.execute(
            select(SessionAgent.agent_id, SessionAgent.model, Agent.model)
            .join(Agent, Agent.id == SessionAgent.agent_id)
            .where(SessionAgent.session_id == session_id))).all()}

        turn_totals = {aid: count for aid, count in (await db.execute(
            select(Turn.speaker_id, func.count(Turn.id)).where(Turn.session_id == session_id)
            .group_by(Turn.speaker_id))).all()}
        seats = (await db.execute(
            select(Agent.role, Agent.id, SessionAgent.muted, SessionAgent.removed_at)
            .select_from(SessionAgent).join(Agent, Agent.id == SessionAgent.agent_id)
            .where(SessionAgent.session_id == session_id)
            .order_by(SessionAgent.seat_order.nulls_last(), Agent.role))).all()
        # The roster: every seat still at the table once the session is under way (a seat added or not yet called has
        # no turns), minus removed ones. Before the start only seats that have spoken are listed, as before.
        turn_counts_rows = [(role, aid, turn_totals.get(aid, 0), muted) for role, aid, muted, removed_at in seats
                            if removed_at is None and (row.status != "created" or aid in turn_totals)]

    usage = await session_usage(sessionmaker, session_id)
    return SessionDetailResponse(session=SessionDetailOut(
        id=row.id, task_id=row.task_id, mode_id=row.mode_id, status=row.status,
        started_at=row.started_at, ended_at=row.ended_at, round=max_round or 0, budget=row.budget, archived_at=row.archived_at,
        usage=UsageOut(tokens_in=usage.tokens_in, tokens_out=usage.tokens_out, cost=usage.cost),
        turn_counts=[TurnCountOut(participant=role, agent_id=agent_id, turns=count, model=seat_models.get(agent_id), muted=muted)
                     for role, agent_id, count, muted in turn_counts_rows],
        failure_reason=(session_failures.get(session_id) or row.failure_reason) if row.status == "failed" else None,
        stage=session_stages.get(session_id) if row.status == "starting" else None,
        stop_reason=row.stop_reason,
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


# How long a client that connects right after /start may wait for the runner to come up.
SSE_RUNTIME_WAIT_SECONDS = 60.0
SSE_POLL_SECONDS = 0.25


async def _wait_for_runtime(request: Request, sessionmaker, session_id: uuid.UUID):
    """The runtime is registered a few seconds after POST /start returns (sandbox create, team
    start), so a client that connects straight away used to find nothing and get an empty
    stream. While the session is still `starting` (or `active` but not yet re-attached after a
    restart), wait for it; any other status means there is nothing live to tail."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SSE_RUNTIME_WAIT_SECONDS
    while True:
        runtime = request.app.state.session_runtimes.get(session_id)
        if runtime is not None:
            return runtime
        async with sessionmaker() as db:
            row = await db.get(Session, session_id)
        if row is None or row.status not in (STATUS_STARTING, STATUS_ACTIVE) or loop.time() >= deadline:
            return None
        if await request.is_disconnected():
            return None
        await asyncio.sleep(SSE_POLL_SECONDS)


def _last_event_id(request: Request) -> int | None:
    raw = request.headers.get("last-event-id")
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


@router.get("/sessions/{session_id}/events")
async def session_events(
    session_id: uuid.UUID,
    request: Request,
    since_seq: int | None = None,
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
) -> EventSourceResponse:
    """Message events (unnamed, with `id: <seq>`) plus the named live events `rolling_summary`,
    `turn_delta`, `turn_tool`, `turn_done`, `turn_error` and `message_meta` (the attachment report
    added to an already-sent message's meta).

    Replay: `since_seq` (or, on an automatic browser reconnect, the `Last-Event-ID` header, which
    takes precedence) replays every stored message after that seq first; `since_seq=0` replays the whole
    transcript. Without either, only live events are sent. The queue is subscribed BEFORE the
    backlog is read and live messages at or below the last replayed seq are skipped, so a
    message published while the backlog is being read is neither lost nor sent twice.

    Ending: when there is nothing live to tail (finished, failed, never started), or the runner
    finishes while connected, the stream drains and sends a final `end` event and closes; the client must close its EventSource on `end`, or
    the browser would reconnect forever. If a slow client's queue overflows the stream also
    closes (no `end`), and the reconnect replays what it missed from the database."""
    # An automatic browser reconnect re-sends the original URL (with its since_seq) plus the id of
    # the last event it received; the header is the more recent of the two, so it wins.
    last_id = _last_event_id(request)
    if last_id is not None:
        since_seq = last_id
    async with sessionmaker() as db:
        if await db.get(Session, session_id) is None:   # an unknown id is a 404, not an empty stream that says "end"
            raise HTTPException(404, detail=_error("not_found", f"session {session_id} not found", "id"))

    async def event_stream():
        runtime = await _wait_for_runtime(request, sessionmaker, session_id)
        # Subscribe first: anything published from here on is queued while we replay.
        queue = runtime.pubsub.subscribe() if runtime is not None and not runtime.runner_task.done() else None
        try:
            last_seq = since_seq if since_seq is not None else -1
            if since_seq is not None:
                async with sessionmaker() as db:
                    rows = (await db.execute(
                        select(Message).where(Message.session_id == session_id, Message.seq > since_seq)
                        .order_by(Message.seq)
                    )).scalars().all()
                for row in rows:
                    if await request.is_disconnected():
                        return
                    last_seq = max(last_seq, row.seq)
                    yield {"id": str(row.seq), "data": json.dumps(_message_row_to_envelope_dict(row))}

            # The rolling summary is persisted on the session, so a client that connects late (or
            # reloads the page) still gets the latest one instead of waiting for the next update.
            async with sessionmaker() as db:
                summary = (await db.get(Session, session_id))
            if summary is not None and summary.rolling_summary:
                yield {"event": "rolling_summary", "data": json.dumps({"summary": summary.rolling_summary})}

            if queue is None:
                yield {"event": "end", "data": "{}"}  # nothing live to tail
                return

            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if runtime.runner_task.done():
                        yield {"event": "end", "data": "{}"}  # queue drained and the runner is over
                        return
                    continue
                kind = event.get("type")
                if kind == "overflow":
                    return  # too slow: close so the client reconnects and replays from the DB
                if kind == "message":
                    data = event["data"]
                    seq = data.get("seq")
                    if seq is not None and since_seq is not None and seq <= last_seq:
                        continue  # already sent as part of the backlog
                    if seq is not None:
                        last_seq = max(last_seq, seq)
                    out = {"data": json.dumps(data)}
                    if seq is not None:
                        out["id"] = str(seq)
                    yield out
                elif kind in ("rolling_summary", "turn_delta", "turn_tool", "turn_done", "turn_error", "message_meta"):
                    yield {"event": kind, "data": json.dumps(event["data"])}
        finally:
            if queue is not None:
                runtime.pubsub.unsubscribe(queue)

    return EventSourceResponse(event_stream())


# ---------------------------------------------------------------- archive / unarchive / delete

# A session in one of these states has a runner, a mirror or a sandbox call in flight.
_BUSY_STATUSES = {STATUS_STARTING, STATUS_ACTIVE, STATUS_STOPPING, STATUS_CANCELLING}


async def _load_idle_session(db, session_id: uuid.UUID, action: str) -> Session:
    row = await db.get(Session, session_id)
    if row is None:
        raise HTTPException(404, detail=_error("not_found", "session not found"))
    if row.status in _BUSY_STATUSES:
        raise HTTPException(409, detail=_error(
            "session_busy", f"can't {action} a session that is {row.status}; pause or end it first"))
    return row


@router.post("/sessions/{session_id}/archive")
async def archive_session(session_id: uuid.UUID,
                          sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> dict:
    """Take a session out of use without deleting it: it leaves the task board, and its summary and synthesis are no
    longer given to later sessions on the task as context. The transcript and the cost record stay. Idempotent."""
    async with sessionmaker() as db:
        row = await _load_idle_session(db, session_id, "archive")
        if row.archived_at is None:
            row.archived_at = datetime.now(timezone.utc)
            audit.emit(action="session.archived", summary="Archived the session", task_id=row.task_id,
                               session_id=session_id, subject_type="session", subject_id=session_id)
            await db.commit()
        return {"session": {"id": str(session_id), "archived_at": row.archived_at.isoformat()}}


@router.post("/sessions/{session_id}/unarchive")
async def unarchive_session(session_id: uuid.UUID,
                            sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> dict:
    async with sessionmaker() as db:
        row = await db.get(Session, session_id)
        if row is None:
            raise HTTPException(404, detail=_error("not_found", "session not found"))
        if row.archived_at is not None:
            row.archived_at = None
            audit.emit(action="session.unarchived", summary="Restored the session", task_id=row.task_id,
                               session_id=session_id, subject_type="session", subject_id=session_id)
        await db.commit()
        return {"session": {"id": str(session_id), "archived_at": None}}


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: uuid.UUID, request: Request,
                         sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> None:
    """Permanently remove a session: its turns, messages, reports, artifacts, usage records and the transcript files.
    Refused while the session is starting, running or ending. A paused session is fine: its runtime is retired first.
    The task, its sandbox and a Build branch in the repository are not touched."""
    async with sessionmaker() as db:
        gone = await _load_idle_session(db, session_id, "delete")
        deleted_task_id, deleted_status = gone.task_id, gone.status
        deleted_turns = await db.scalar(select(func.count()).select_from(Turn).where(Turn.session_id == session_id))

    runtime = request.app.state.session_runtimes.pop(session_id, None)
    if runtime is not None:
        await _retire_runtime(runtime)
    request.app.state.running_sessions.pop(session_id, None)
    session_failures.pop(session_id, None)
    session_stages.pop(session_id, None)

    async with sessionmaker() as db:
        # Children first: usage and artifacts point at turns, reports at messages.
        # A report's own session_id can be null, so find reports through the session's messages too.
        await db.execute(delete(Report).where(or_(
            Report.session_id == session_id,
            Report.message_id.in_(select(Message.message_id).where(Message.session_id == session_id)))))
        for model in (UsageLedger, Artifact):
            await db.execute(delete(model).where(model.session_id == session_id))
        for model in (Message, Turn, SessionAgent):
            await db.execute(delete(model).where(model.session_id == session_id))
        await db.execute(delete(Session).where(Session.id == session_id))
        audit.emit(action="session.deleted", summary="Deleted the session and its transcript",
                           task_id=deleted_task_id, session_id=session_id, subject_type="session", subject_id=session_id,
                           detail={"turns": deleted_turns, "status": deleted_status})
        await db.commit()

    root = factory_root().resolve()
    folder = (root / str(session_id)).resolve()
    if folder.parent == root and folder.is_dir():
        await asyncio.to_thread(shutil.rmtree, folder, True)
