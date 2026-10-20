"""POST /tasks (M2.8).

Zero auth: `owner_id` is client-supplied and never verified against a caller identity --
there is no caller identity yet (that's M5). Anyone who can reach this port can create
tasks. See docs/decisions.md and README.md.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models import Artifact, Session, Task, UsageLedger
from ..build_policy import parse_test_command
from ..workspace import WorkspaceReport, preflight_workspace
from .deps import get_db_sessionmaker
from .types import BRIEF_MAX, CRITERIA_MAX, OUTPUT_TYPE_MAX, TITLE_MAX, text

router = APIRouter()


class WorkspaceIn(BaseModel):
    """Build mode: the local repository the team may change (docs/build-mode-design.md)."""
    repo_path: text(1024)
    base_branch: text(200) = "main"
    test_command: text(500) | None = None


class WorkspacePreflightOut(BaseModel):
    ok: bool
    problems: list[dict]
    repo_root: str | None
    current_branch: str | None
    dirty: bool
    dirty_files: list[str]


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
    rep = await asyncio.to_thread(preflight_workspace, ws.repo_path, ws.base_branch)
    if not rep.ok:
        raise HTTPException(status_code=422, detail={"error": {
            "code": "workspace_" + rep.problems[0]["code"], "detail": rep.problems[0]["detail"], "field": "workspace"}})
    return {"repo_path": rep.repo_root, "base_branch": ws.base_branch, "test_command": ws.test_command}


class TaskCreateRequest(BaseModel):
    title: text(TITLE_MAX)
    brief: text(BRIEF_MAX)
    output_type: text(OUTPUT_TYPE_MAX)
    success_criteria: text(CRITERIA_MAX) | None = None
    owner_id: uuid.UUID | None = None
    workspace: WorkspaceIn | None = None


class TaskOut(BaseModel):
    id: uuid.UUID
    title: str
    brief: str | None
    output_type: str
    success_criteria: str | None
    owner_id: uuid.UUID | None
    workspace: dict | None = None
    created_at: datetime


class TaskCreateResponse(BaseModel):
    task: TaskOut


@router.post("/tasks", status_code=201, response_model=TaskCreateResponse)
async def create_task(
    req: TaskCreateRequest,
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
) -> TaskCreateResponse:
    workspace = await _validated_workspace(req.workspace) if req.workspace is not None else None
    async with sessionmaker() as db:
        row = Task(title=req.title, brief=req.brief, output_type=req.output_type,
                    success_criteria=req.success_criteria, owner_id=req.owner_id, workspace=workspace)
        db.add(row)
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
        await db.commit()
        await db.refresh(row)
        return TaskCreateResponse(task=TaskOut.model_validate(row, from_attributes=True))


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
