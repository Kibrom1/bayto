"""GET /agents/templates (M3.1): the M2.11 seeded agent templates for the session-setup
screen's Agent explorer.

Filtered to `kind="native"` -- "templates" specifically means the 6 reusable M2.11
personas, distinct from external/remote/packaged-imported agents (a future `kind` value).
`model` is the raw stored `Agent.model` value, not a synthesized "tier" label -- there's no
tier-computation logic anywhere in the system (M2.11 already treated per-agent model choice
as content, defaulting to one uniform string); expose whatever was actually seeded.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models import Agent
from .deps import get_db_sessionmaker

router = APIRouter()


class AgentTemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    role: str | None
    system_prompt: str | None
    stance: str | None
    model: str | None


@router.get("/agents/templates", response_model=list[AgentTemplateOut])
async def list_agent_templates(sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> list[AgentTemplateOut]:
    async with sessionmaker() as db:
        rows = (await db.execute(select(Agent).where(Agent.kind == "native"))).scalars().all()
    return [AgentTemplateOut.model_validate(row, from_attributes=True) for row in rows]
