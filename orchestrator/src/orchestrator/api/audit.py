"""Read the audit trail: list with filters, verify the chain, export as CSV. There is no write endpoint: events are
only ever recorded by the action they describe (audit.record)."""
from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .. import audit
from ..models import AuditEvent
from .deps import get_db_sessionmaker

router = APIRouter()

MAX_PAGE = 500


class AuditOut(BaseModel):
    seq: int
    at: datetime
    actor: str
    action: str
    summary: str
    task_id: uuid.UUID | None
    session_id: uuid.UUID | None
    subject_type: str | None
    subject_id: str | None
    detail: dict


def _query(task_id, session_id, action):
    q = select(AuditEvent)
    if task_id:
        q = q.where(AuditEvent.task_id == task_id)
    if session_id:
        q = q.where(AuditEvent.session_id == session_id)
    if action:
        # "agent" matches agent.created, agent.updated...; "agent.created" matches exactly that.
        q = q.where(AuditEvent.action.like(f"{action}.%") | (AuditEvent.action == action))
    return q


@router.get("/audit", response_model=list[AuditOut])
async def list_events(task_id: uuid.UUID | None = None, session_id: uuid.UUID | None = None, action: str | None = None,
                      before: int | None = Query(default=None, description="only events older than this seq"),
                      limit: int = Query(default=100, ge=1, le=MAX_PAGE),
                      sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> list[AuditOut]:
    q = _query(task_id, session_id, action)
    if before is not None:
        q = q.where(AuditEvent.seq < before)
    async with sessionmaker() as db:
        rows = (await db.execute(q.order_by(AuditEvent.seq.desc()).limit(limit))).scalars().all()
    return [AuditOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/audit/verify")
async def verify_chain(sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> dict:
    async with sessionmaker() as db:
        return await audit.verify(db)


@router.get("/audit/export")
async def export_csv(task_id: uuid.UUID | None = None, session_id: uuid.UUID | None = None, action: str | None = None,
                     sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> Response:
    async with sessionmaker() as db:
        rows = (await db.execute(_query(task_id, session_id, action).order_by(AuditEvent.seq).limit(50_000))).scalars().all()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["seq", "at", "actor", "action", "summary", "task_id", "session_id", "subject_type", "subject_id", "detail"])
    for r in rows:
        # A cell that starts with = + - @ would run as a formula in a spreadsheet: prefix it.
        safe = lambda v: ("'" + v) if isinstance(v, str) and v[:1] in "=+-@\t\r" and v else v  # noqa: E731
        w.writerow([r.seq, r.at.isoformat(), r.actor, r.action, safe(r.summary), r.task_id or "", r.session_id or "",
                    r.subject_type or "", safe(r.subject_id or ""), safe(json.dumps(r.detail, sort_keys=True))])
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="bayto-audit.csv"'})
