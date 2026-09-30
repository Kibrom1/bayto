"""GET /modes (M3.1): tunable-parameter defaults for the session-setup screen.

Reads `modes/<name>.yaml` live via `resolve_mode()`, NOT `Mode.stop_rules_json` -- the
YAML file is the source of truth everywhere else in this system (M2.8/M2.9's established
principle), and the DB row's `stop_rules_json` is not automatically kept in sync with it
(disclosed in M2.11's docs/decisions.md). Scoped to just floor_policy/stop_rules -- not
roles/route/moderator from ModeConfig, since those are internal wiring a session-setup UI
doesn't need.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models import Mode
from ..modes_registry import ModeNotFoundError, ModeParseError, resolve_mode
from .deps import get_db_sessionmaker

log = logging.getLogger(__name__)

router = APIRouter()


class StopRulesOut(BaseModel):
    max_rounds: int | None
    converge_after_quiet_rounds: int
    stale_argument_turns: int
    urgency_boost_multiplier: float
    max_consecutive_grants: int
    urgency_boost_reasons: list[str]


class ModeOut(BaseModel):
    id: uuid.UUID
    name: str
    floor_policy: str
    stop_rules: StopRulesOut


def _stop_rules_out(raw: dict | None) -> StopRulesOut:
    """`ModeConfig.stop_rules` is a raw, possibly-partial dict (agent-comms doesn't type
    it -- see acp.modes's module docstring); missing keys fall back to the same
    product-owner-confirmed defaults `StopRulesConfig` itself uses (M2.7)."""
    raw = raw or {}
    return StopRulesOut(
        max_rounds=raw.get("max_rounds"),
        converge_after_quiet_rounds=raw.get("converge_after_quiet_rounds", 2),
        stale_argument_turns=raw.get("stale_argument_turns", 3),
        urgency_boost_multiplier=raw.get("urgency_boost_multiplier", 1.3),
        max_consecutive_grants=raw.get("max_consecutive_grants", 2),
        urgency_boost_reasons=raw.get("urgency_boost_reasons", ["new_point", "disagree"]),
    )


@router.get("/modes", response_model=list[ModeOut])
async def list_modes(sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> list[ModeOut]:
    async with sessionmaker() as db:
        rows = (await db.execute(select(Mode))).scalars().all()

    out = []
    for row in rows:
        try:
            cfg = resolve_mode(row.name)
        except (ModeNotFoundError, ModeParseError) as exc:
            # Same failure-isolation principle as M2.9's reconcile routine: one broken/
            # missing mode file must not 500 the whole list for every other mode.
            log.warning("skipping mode %r (%s) from GET /modes: %s", row.name, row.id, exc)
            continue
        out.append(ModeOut(id=row.id, name=cfg.name, floor_policy=cfg.floor_policy,
                            stop_rules=_stop_rules_out(cfg.stop_rules)))
    return out
