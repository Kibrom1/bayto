"""Agents: the catalogue for the session-setup screen (M3.1) and custom agents.

`GET /agents/templates` lists the seeded personas (`kind="native"`) and the user's own agents
(`kind="custom"`), leaving archived ones out. External/remote agents (a future `kind`) are not
listed. `model` is the raw stored value, not a synthesized tier label.

Custom agents are created from a role, a stance and instructions plus a permission *preset*.
The preset, not the client, decides the tool list, so a custom agent can never be handed a tool
this module did not define. Today there is one preset, `discussion`: the seat takes part in the
conversation and can read the project folder (the sandbox's default), but cannot write files,
run commands or use the network. Custom agents cannot join Build: that mode is a fixed pipeline
of four seeded roles, and an agent that edits files has no place in a discussion.

Agents are archived, never deleted: sessions and turns keep pointing at them.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .. import audit
from ..models import Agent
from ..seed import AGENT_MODEL
from .deps import get_db_sessionmaker
from .types import text

router = APIRouter()

KIND_NATIVE = "native"
KIND_CUSTOM = "custom"

# preset id -> the tools the agent is given on top of the sandbox's read-only default (Read, Grep, Glob).
PRESETS: dict[str, dict] = {
    "discussion": {
        "label": "Discussion only",
        "description": "Takes part in the conversation and can read the project folder. Cannot change files, run commands or use the network.",
        "tools": [],
    },
}
DEFAULT_PRESET = "discussion"

NAME_MAX = 60
STANCE_MAX = 60
INSTRUCTIONS_MAX = 4_000

# Roles the orchestrator, the modes or Build address by name; a custom agent may not take one.
RESERVED_ROLES = frozenset({"moderator", "human", "coordinator", "supervisor", "system", "all", "everyone"})


def _error(code: str, detail: str, field: str | None = None) -> dict:
    body: dict = {"code": code, "detail": detail}
    if field is not None:
        body["field"] = field
    return {"error": body}


def role_slug(name: str) -> str:
    return re.sub(r"[\W_]+", "-", name.lower()).strip("-")


class AgentOut(BaseModel):
    id: uuid.UUID
    name: str
    role: str | None
    system_prompt: str | None
    stance: str | None
    model: str | None
    kind: str
    custom: bool
    preset: str | None


def _out(row: Agent) -> AgentOut:
    custom = row.kind == KIND_CUSTOM
    return AgentOut(
        id=row.id, name=row.name, role=row.role, system_prompt=row.system_prompt, stance=row.stance,
        model=row.model, kind=row.kind, custom=custom,
        preset=(row.params_json or {}).get("preset") if custom else None,
    )


class AgentCreate(BaseModel):
    name: text(NAME_MAX)
    stance: text(STANCE_MAX)
    instructions: text(INSTRUCTIONS_MAX)
    preset: Literal["discussion"] = DEFAULT_PRESET


class AgentUpdate(BaseModel):
    name: text(NAME_MAX) | None = None
    stance: text(STANCE_MAX) | None = None
    instructions: text(INSTRUCTIONS_MAX) | None = None


@router.get("/agents/templates", response_model=list[AgentOut])
async def list_agent_templates(sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> list[AgentOut]:
    async with sessionmaker() as db:
        rows = (await db.execute(
            select(Agent).where(Agent.kind.in_([KIND_NATIVE, KIND_CUSTOM]), Agent.archived_at.is_(None))
            .order_by(Agent.kind.desc(), Agent.name)  # "native" before "custom"
        )).scalars().all()
    return [_out(row) for row in rows]


@router.get("/agents/presets")
async def list_presets() -> list[dict]:
    return [{"id": pid, "label": p["label"], "description": p["description"]} for pid, p in PRESETS.items()]


async def _check_name_and_role(db, name: str, *, exclude: uuid.UUID | None = None, check_role: bool = True) -> str:
    role = role_slug(name)
    if len(role) < 2:
        raise HTTPException(422, detail=_error("invalid_name", "the name needs at least two letters or digits", "name"))
    if role in RESERVED_ROLES or role.startswith("build-"):
        raise HTTPException(422, detail=_error("reserved_name", f"'{name}' is reserved; pick another name", "name"))
    rows = (await db.execute(select(Agent.id, Agent.name, Agent.role))).all()
    for rid, rname, rrole in rows:
        if rid == exclude:
            continue
        if rname.lower() == name.lower() or (check_role and rrole == role):
            raise HTTPException(409, detail=_error(
                "name_taken", f"an agent called '{rname}' already exists (it may be archived)", "name"))
    return role


@router.post("/agents", status_code=201, response_model=AgentOut)
async def create_agent(req: AgentCreate, sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> AgentOut:
    async with sessionmaker() as db:
        role = await _check_name_and_role(db, req.name)
        row = Agent(
            kind=KIND_CUSTOM, name=req.name, role=role, system_prompt=req.instructions, stance=req.stance,
            model=AGENT_MODEL, params_json={"preset": req.preset}, tools=list(PRESETS[req.preset]["tools"]), version="1",
        )
        db.add(row)
        await db.flush()
        audit.emit(action="agent.created", summary=f"Created custom agent {row.name}",
                           subject_type="agent", subject_id=row.id,
                           detail={"name": row.name, "role": row.role, "stance": row.stance, "preset": req.preset,
                                   "tools": row.tools, "instructions_chars": len(req.instructions),
                                   "instructions_sha256": audit.sha256(req.instructions)})
        await db.commit()
        await db.refresh(row)
        return _out(row)


async def _get_custom(db, agent_id: uuid.UUID) -> Agent:
    row = await db.get(Agent, agent_id)
    if row is None or row.archived_at is not None:
        raise HTTPException(404, detail=_error("not_found", "agent not found"))
    if row.kind != KIND_CUSTOM:
        raise HTTPException(409, detail=_error("built_in_agent", "built-in agents cannot be changed"))
    return row


@router.patch("/agents/{agent_id}", response_model=AgentOut)
async def update_agent(agent_id: uuid.UUID, req: AgentUpdate,
                       sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> AgentOut:
    async with sessionmaker() as db:
        row = await _get_custom(db, agent_id)
        changed = []
        if req.name is not None and req.name != row.name:
            # The role is the agent's identity in transcripts and the moderator's addressing, so a rename keeps it.
            await _check_name_and_role(db, req.name, exclude=row.id, check_role=False)
            row.name = req.name
            changed.append("name")
        if req.stance is not None and req.stance != row.stance:
            row.stance = req.stance
            changed.append("stance")
        if req.instructions is not None and req.instructions != row.system_prompt:
            row.system_prompt = req.instructions
            changed.append("instructions")
        row.version = str(int(row.version or "1") + 1)
        audit.emit(action="agent.updated", summary=f"Edited agent {row.name} ({', '.join(changed) or 'no change'})",
                           subject_type="agent", subject_id=row.id,
                           detail={"changed": changed, "version": row.version,
                                   "instructions_sha256": audit.sha256(row.system_prompt or "")})
        await db.commit()
        await db.refresh(row)
        return _out(row)


@router.delete("/agents/{agent_id}", status_code=204)
async def archive_agent(agent_id: uuid.UUID, sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> Response:
    async with sessionmaker() as db:
        row = await _get_custom(db, agent_id)
        row.archived_at = datetime.now(timezone.utc)
        audit.emit(action="agent.archived", summary=f"Archived agent {row.name}",
                           subject_type="agent", subject_id=row.id, detail={"name": row.name, "role": row.role})
        await db.commit()
    return Response(status_code=204)
