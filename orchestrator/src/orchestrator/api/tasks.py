"""POST /tasks (M2.8).

Zero auth: `owner_id` is client-supplied and never verified against a caller identity --
there is no caller identity yet (that's M5). Anyone who can reach this port can create
tasks. See docs/decisions.md and README.md.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models import Artifact, Session, Task
from .deps import get_db_sessionmaker

router = APIRouter()


class TaskCreateRequest(BaseModel):
    title: str
    brief: str
    output_type: str
    success_criteria: str | None = None
    owner_id: uuid.UUID | None = None


class TaskOut(BaseModel):
    id: uuid.UUID
    title: str
    brief: str | None
    output_type: str
    success_criteria: str | None
    owner_id: uuid.UUID | None
    created_at: datetime


class TaskCreateResponse(BaseModel):
    task: TaskOut


@router.post("/tasks", status_code=201, response_model=TaskCreateResponse)
async def create_task(
    req: TaskCreateRequest,
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
) -> TaskCreateResponse:
    async with sessionmaker() as db:
        row = Task(title=req.title, brief=req.brief, output_type=req.output_type,
                    success_criteria=req.success_criteria, owner_id=req.owner_id)
        db.add(row)
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
                select(Session).where(Session.task_id == task.id)
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
                output_artifact_id=output_artifact_id,
            ))
        return out

@router.get("/tasks/{task_id}/usage-report", response_model=dict)
async def get_task_usage_report(
    task_id: uuid.UUID,
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
) -> dict:
    """M6: detailed usage ledger for a task across all its sessions."""
    async with sessionmaker() as db:
        # Find all sessions for this task
        sessions = (await db.execute(
            select(Session.id).where(Session.task_id == task_id)
        )).scalars().all()

        if not sessions:
            return {"error": "no sessions found for this task"}

        # Aggregate ledger entries
        from ..models import UsageLedger
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
