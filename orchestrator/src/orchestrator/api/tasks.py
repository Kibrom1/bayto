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
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models import Task
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
