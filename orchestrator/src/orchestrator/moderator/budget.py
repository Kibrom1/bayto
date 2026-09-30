"""Session token/cost usage (M2.7, extracted for M3.1's `GET /sessions/{id}` endpoint):
one shared aggregate query, not a second copy that could silently drift from
`ModeratorRunner._budget_exceeded`'s. Accuracy is bounded by the same self-reported-meta
convention as the budget check itself (docs/agent-communication-protocol.md's
`tokens_in`/`tokens_out`/`cost` section) -- a turn that never reported them counts as 0.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models import Turn


class SessionUsage(BaseModel):
    tokens_in: int
    tokens_out: int
    cost: float


async def session_usage(sessionmaker: async_sessionmaker, session_id: uuid.UUID) -> SessionUsage:
    async with sessionmaker() as session:
        tokens_in, tokens_out, cost = (await session.execute(
            select(func.coalesce(func.sum(Turn.tokens_in), 0), func.coalesce(func.sum(Turn.tokens_out), 0),
                   func.coalesce(func.sum(Turn.cost), 0))
            .where(Turn.session_id == session_id)
        )).one()
        return SessionUsage(tokens_in=tokens_in, tokens_out=tokens_out, cost=float(cost))
