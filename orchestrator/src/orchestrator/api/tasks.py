"""POST /tasks (M2.8).

Zero auth: `owner_id` is client-supplied and never verified against a caller identity --
there is no caller identity yet (that's M5). Anyone who can reach this port can create
tasks. See docs/decisions.md and README.md.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .. import audit
from .. import local_fallback as lf
from ..providers import provider_id_of
from ..models import Artifact, Session, Task, TaskAttachment, UsageLedger
from ..providers.embeddings import NEEDS, embedding_model, embedding_provider
from ..build_policy import parse_test_command, permission_summary, suggest_test_command, validate_extra_commands
from ..workspace import WorkspaceReport, preflight_workspace
from .deps import get_db_sessionmaker
from .types import BRIEF_MAX, CRITERIA_MAX, OUTPUT_TYPE_MAX, TITLE_MAX, text

router = APIRouter()


class WorkspaceIn(BaseModel):
    """Build mode: the local repository the team may change (docs/build-mode-design.md)."""
    repo_path: text(1024)
    base_branch: text(200) = "main"
    test_command: text(500) | None = None
    # More commands the developer and tester may run (a linter, a setup step), checked like the test command.
    allowed_commands: list[text(500)] = Field(default_factory=list, max_length=10)


class WorkspacePreflightOut(BaseModel):
    ok: bool
    problems: list[dict]
    repo_root: str | None
    current_branch: str | None
    dirty: bool
    dirty_files: list[str]
    suggested_test_command: str | None = None
    permissions: list[dict] = Field(default_factory=list)


def _report_out(rep: WorkspaceReport) -> WorkspacePreflightOut:
    return WorkspacePreflightOut(ok=rep.ok, problems=rep.problems, repo_root=rep.repo_root,
                                 current_branch=rep.current_branch, dirty=rep.dirty, dirty_files=rep.dirty_files)


@router.post("/workspaces/preflight", response_model=WorkspacePreflightOut)
async def preflight(ws: WorkspaceIn) -> WorkspacePreflightOut:
    """Check a repo path before a task uses it. Local-only service with no auth (see module
    docstring): it reveals whether a path is a git repo, which is fine on the developer's machine."""
    out = _report_out(await asyncio.to_thread(preflight_workspace, ws.repo_path, ws.base_branch))
    if ws.test_command:
        # Creating the task refuses a bad test command (see _validated_workspace); say so here too, so the form
        # does not report "ready" for something the Create button will then reject.
        try:
            parse_test_command(ws.test_command)
        except ValueError as exc:
            out.ok = False
            out.problems = [{"code": "bad_test_command", "detail": str(exc)}, *out.problems]
    try:
        validate_extra_commands(ws.allowed_commands)
    except ValueError as exc:
        out.ok = False
        out.problems = [{"code": "bad_allowed_commands", "detail": str(exc)}, *out.problems]
    if out.repo_root:
        out.suggested_test_command = await asyncio.to_thread(suggest_test_command, out.repo_root)
    if out.ok:
        out.permissions = permission_summary({"test_command": ws.test_command, "allowed_commands": ws.allowed_commands})
    return out


async def _validated_workspace(ws: WorkspaceIn) -> dict:
    """Shared by POST /tasks and PUT /tasks/{id}/workspace. Dirty trees pass here (a Build session checks again
    when it starts); a bad test command or an unusable repo is a 422 with a code the UI can show."""
    if ws.test_command:
        try:
            parse_test_command(ws.test_command)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"error": {
                "code": "workspace_bad_test_command", "detail": str(exc), "field": "workspace"}})
    try:
        extra = validate_extra_commands(ws.allowed_commands)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": {
            "code": "workspace_bad_allowed_commands", "detail": str(exc), "field": "workspace"}})
    rep = await asyncio.to_thread(preflight_workspace, ws.repo_path, ws.base_branch)
    if not rep.ok:
        raise HTTPException(status_code=422, detail={"error": {
            "code": "workspace_" + rep.problems[0]["code"], "detail": rep.problems[0]["detail"], "field": "workspace"}})
    return {"repo_path": rep.repo_root, "base_branch": ws.base_branch, "test_command": ws.test_command,
            "allowed_commands": extra}


class LocalFallbackIn(BaseModel):
    enabled: bool
    model: str | None = None  # an ollama/... catalogue model; null = BAYTO_LOCAL_FALLBACK_MODEL


class TaskCreateRequest(BaseModel):
    title: text(TITLE_MAX)
    brief: text(BRIEF_MAX)
    output_type: text(OUTPUT_TYPE_MAX)
    success_criteria: text(CRITERIA_MAX) | None = None
    owner_id: uuid.UUID | None = None
    workspace: WorkspaceIn | None = None
    # Opt in to meaning-based ranking of long attachments. Sends attachment text to the embedding provider.
    semantic_retrieval: bool = False
    # Opt in to a local model taking over a seat when Claude runs out of usage. Off unless given.
    local_fallback: LocalFallbackIn | None = None


class TaskOut(BaseModel):
    id: uuid.UUID
    title: str
    brief: str | None
    output_type: str
    success_criteria: str | None
    owner_id: uuid.UUID | None
    workspace: dict | None = None
    semantic_retrieval: bool = False
    local_fallback: dict | None = None
    created_at: datetime


class TaskCreateResponse(BaseModel):
    task: TaskOut


@router.post("/tasks", status_code=201, response_model=TaskCreateResponse)
async def create_task(
    req: TaskCreateRequest,
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
) -> TaskCreateResponse:
    workspace = await _validated_workspace(req.workspace) if req.workspace is not None else None
    fallback = _checked_fallback(req.local_fallback, build=workspace is not None) if req.local_fallback is not None else None
    async with sessionmaker() as db:
        row = Task(title=req.title, brief=req.brief, output_type=req.output_type,
                    success_criteria=req.success_criteria, owner_id=req.owner_id, workspace=workspace,
                    semantic_retrieval=req.semantic_retrieval, local_fallback=fallback)
        db.add(row)
        await db.flush()
        audit.emit(action="task.created", summary=f"Created task {row.title}", task_id=row.id,
                           subject_type="task", subject_id=row.id,
                           detail={"title": row.title, "output_type": row.output_type, "build": workspace is not None,
                                   "semantic_retrieval": row.semantic_retrieval})
        await db.commit()
        await db.refresh(row)
        return TaskCreateResponse(task=TaskOut.model_validate(row, from_attributes=True))


@router.put("/tasks/{task_id}/workspace", response_model=TaskCreateResponse)
async def set_task_workspace(
    task_id: uuid.UUID, ws: WorkspaceIn,
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
) -> TaskCreateResponse:
    """Give an existing task a repository, for example to continue a finished discussion as a Build."""
    workspace = await _validated_workspace(ws)
    async with sessionmaker() as db:
        row = await db.get(Task, task_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"error": {"code": "task_not_found", "detail": "task not found"}})
        row.workspace = workspace
        audit.emit(action="task.workspace_set", summary=f"Set the repository for {row.title}", task_id=row.id,
                           subject_type="task", subject_id=row.id,
                           detail={"repo_path": workspace.get("repo_path"), "base_branch": workspace.get("base_branch"),
                                   "test_command": workspace.get("test_command")})
        await db.commit()
        await db.refresh(row)
        return TaskCreateResponse(task=TaskOut.model_validate(row, from_attributes=True))


# ---------------------------------------------------------------- semantic retrieval switch

class RetrievalIn(BaseModel):
    semantic_retrieval: bool


class RetrievalOut(BaseModel):
    semantic_retrieval: bool
    ready: bool            # an embedding provider is configured on this machine
    provider: str | None   # "openai" | "ollama" | None
    model: str | None
    needs: str | None      # what to set when not ready


def _retrieval_out(task: Task) -> RetrievalOut:
    provider = embedding_provider()
    return RetrievalOut(semantic_retrieval=task.semantic_retrieval, ready=provider is not None, provider=provider,
                        model=embedding_model(provider) if provider else None, needs=None if provider else NEEDS)


@router.get("/tasks/{task_id}/retrieval", response_model=RetrievalOut)
async def get_task_retrieval(task_id: uuid.UUID,
                             sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> RetrievalOut:
    async with sessionmaker() as db:
        row = await db.get(Task, task_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"error": {"code": "task_not_found", "detail": "task not found"}})
        return _retrieval_out(row)


@router.put("/tasks/{task_id}/retrieval", response_model=RetrievalOut)
async def set_task_retrieval(task_id: uuid.UUID, req: RetrievalIn,
                             sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> RetrievalOut:
    """Turn the semantic stage on or off. Takes effect on each seat's next turn (the prompt is rebuilt every turn).
    Allowed whether or not an embedding provider is set up: without one, turns quietly rank by keyword."""
    async with sessionmaker() as db:
        row = await db.get(Task, task_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"error": {"code": "task_not_found", "detail": "task not found"}})
        if row.semantic_retrieval != req.semantic_retrieval:
            row.semantic_retrieval = req.semantic_retrieval
            audit.emit(action="task.semantic_retrieval_set",
                       summary=("Turned on" if req.semantic_retrieval else "Turned off") + f" semantic retrieval for {row.title}",
                       task_id=row.id, subject_type="task", subject_id=row.id,
                       detail={"semantic_retrieval": req.semantic_retrieval})
        await db.commit()
        await db.refresh(row)
        return _retrieval_out(row)


# ---------------------------------------------------------------- local fallback switch

class LocalFallbackOut(BaseModel):
    enabled: bool
    model: str | None          # as stored (null = use the default)
    effective_model: str | None  # what a switch would actually run on
    default_model: str | None  # BAYTO_LOCAL_FALLBACK_MODEL
    ready: bool                # the effective model could run now (catalogue + OLLAMA_HOST)
    needs: str | None          # why not, in words
    build: bool                # Build tasks never fall back; the setting is stored but ignored


def _checked_fallback(req: LocalFallbackIn, *, build: bool) -> dict:
    """The value to store. Turning it on needs a usable local model; turning it off never fails (and keeps the choice)."""
    model = (req.model or "").strip() or None
    if req.enabled:
        problem = lf.model_problem(model or lf.env_default_model())
        if problem:
            raise HTTPException(status_code=422, detail={"error": {"code": problem[0], "detail": problem[1], "field": "local_fallback"}})
    elif model is not None and provider_id_of(model) != lf.PROVIDER:
        raise HTTPException(status_code=422, detail={"error": {"code": "invalid_model",
                            "detail": f"{model} is not a local Ollama model", "field": "local_fallback"}})
    return {"enabled": req.enabled, "model": model}


def _fallback_out(task: Task) -> LocalFallbackOut:
    cfg = lf.resolve(task.local_fallback)
    problem = lf.model_problem(cfg.model)
    return LocalFallbackOut(enabled=cfg.enabled, model=(task.local_fallback or {}).get("model"), effective_model=cfg.model,
                            default_model=lf.env_default_model(), ready=problem is None, needs=problem[1] if problem else None,
                            build=task.workspace is not None)


@router.get("/tasks/{task_id}/local-fallback", response_model=LocalFallbackOut)
async def get_task_local_fallback(task_id: uuid.UUID,
                                  sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> LocalFallbackOut:
    async with sessionmaker() as db:
        row = await db.get(Task, task_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"error": {"code": "task_not_found", "detail": "task not found"}})
        return _fallback_out(row)


@router.put("/tasks/{task_id}/local-fallback", response_model=LocalFallbackOut)
async def set_task_local_fallback(task_id: uuid.UUID, req: LocalFallbackIn,
                                  sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> LocalFallbackOut:
    """Opt a task in or out of the local fallback. Takes effect on each seat's next turn. Build tasks accept the setting
    but never use it: a Build seat needs tools, which a local text model does not have."""
    async with sessionmaker() as db:
        row = await db.get(Task, task_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"error": {"code": "task_not_found", "detail": "task not found"}})
        value = _checked_fallback(req, build=row.workspace is not None)
        if value != (row.local_fallback or {"enabled": False, "model": None}):
            row.local_fallback = value
            audit.emit(action="task.local_fallback_set",
                       summary=("Turned on" if req.enabled else "Turned off") + f" the local model fallback for {row.title}",
                       task_id=row.id, subject_type="task", subject_id=row.id, detail=value)
        await db.commit()
        await db.refresh(row)
        return _fallback_out(row)


# ---------------------------------------------------------------- DELETE /tasks/{id}

@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: uuid.UUID, request: Request,
                      sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> Response:
    """Remove a task and its sessions. Refused while any session is starting, running or ending."""
    from .sessions import _BUSY_STATUSES, delete_session  # sessions imports this module's helpers too
    async with sessionmaker() as db:
        row = await db.get(Task, task_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"error": {"code": "task_not_found", "detail": "task not found"}})
        sessions = (await db.execute(select(Session.id, Session.status).where(Session.task_id == task_id))).all()
        if any(status in _BUSY_STATUSES for _, status in sessions):
            raise HTTPException(status_code=409, detail={"error": {"code": "task_busy",
                                "detail": "a session on this task is still running; pause or end it first"}})
    for sid, _ in sessions:
        await delete_session(sid, request, sessionmaker)
    async with sessionmaker() as db:
        row = await db.get(Task, task_id)
        await db.execute(delete(TaskAttachment).where(TaskAttachment.task_id == task_id))
        audit.emit(action="task.deleted", summary=f"Deleted {row.title}", task_id=row.id, subject_type="task",
                   subject_id=row.id, detail={"sessions": len(sessions)})
        await db.delete(row)
        await db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- GET /tasks

class TaskListOut(BaseModel):
    id: uuid.UUID
    title: str
    brief: str | None
    output_type: str
    # Derived from the last session's Session.status, not a Task column (Task has none) --
    # null if the task has no sessions yet.
    status: str | None
    last_session_id: uuid.UUID | None
    output_artifact_id: uuid.UUID | None
    workspace: dict | None = None  # Build mode: {repo_path, base_branch, test_command}
    semantic_retrieval: bool = False
    local_fallback: dict | None = None


@router.get("/tasks", response_model=list[TaskListOut])
async def list_tasks(sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> list[TaskListOut]:
    async with sessionmaker() as db:
        tasks = (await db.execute(select(Task))).scalars().all()
        out = []
        for task in tasks:
            # "Last" means last activity, not last row inserted: order by started_at so an
            # unstarted session (started_at is null) never outranks one that's actually
            # run, UNLESS it's literally the only session the task has (NULLS LAST puts it
            # last among several, but it's still the one and only row when there's just
            # one). See docs/decisions.md if this ever needs a real created_at tiebreak.
            last_session = (await db.execute(
                select(Session).where(Session.task_id == task.id, Session.archived_at.is_(None))
                .order_by(Session.started_at.desc().nullslast())
                .limit(1)
            )).scalar_one_or_none()

            output_artifact_id = None
            if last_session is not None:
                output_artifact_id = await db.scalar(
                    select(Artifact.id).where(Artifact.session_id == last_session.id,
                                               Artifact.type == "synthesis")
                )

            out.append(TaskListOut(
                id=task.id, title=task.title, brief=task.brief, output_type=task.output_type,
                status=last_session.status if last_session is not None else None,
                last_session_id=last_session.id if last_session is not None else None,
                output_artifact_id=output_artifact_id, workspace=task.workspace,
                semantic_retrieval=task.semantic_retrieval, local_fallback=task.local_fallback,
            ))
        return out

@router.get("/tasks/{task_id}/usage-report", response_model=dict)
async def get_task_usage_report(
    task_id: uuid.UUID,
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
) -> dict:
    """M6: detailed usage ledger for a task across all its sessions."""
    async with sessionmaker() as db:
        if await db.get(Task, task_id) is None:
            raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "detail": "task not found"}})

        # Find all sessions for this task
        sessions = (await db.execute(
            select(Session.id).where(Session.task_id == task_id)
        )).scalars().all()

        # Aggregate ledger entries
        ledger_rows = (await db.execute(
            select(UsageLedger).where(UsageLedger.session_id.in_(sessions))
            .order_by(UsageLedger.timestamp)
        )).scalars().all()

        return {
            "task_id": str(task_id),
            "total_tokens_in": sum(l.tokens_in for l in ledger_rows),
            "total_tokens_out": sum(l.tokens_out for l in ledger_rows),
            "total_cost": float(sum(l.cost for l in ledger_rows)),
            "ledger": [
                {"session_id": str(l.session_id), "tokens_in": l.tokens_in, "tokens_out": l.tokens_out, "cost": float(l.cost), "timestamp": l.timestamp.isoformat()}
                for l in ledger_rows
            ]
        }
