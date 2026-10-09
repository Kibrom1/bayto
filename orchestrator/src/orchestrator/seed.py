"""Idempotent seed data (M2.11): six agent templates, three discussion modes
(open-chat/debate/brainstorm). Run via `python -m orchestrator.seed`.

Deliberately a plain script, not an Alembic data migration -- schema and content change for
different reasons and at different rates (same "config that changes independently of code
shouldn't be frozen into migration history" reasoning as M2.5's mode YAML files). An agent
template's system_prompt is expected to get iterated on for quality; baking every revision
into a new migration would bloat schema-migration history with content churn.

Idempotency: upsert by natural key (`Agent.name`, `Mode.name`) via
`INSERT ... ON CONFLICT (name) DO UPDATE`, so re-running this after a content tweak updates
the existing row (keeping its `id`) instead of erroring or duplicating. This needed a real
unique constraint on both columns -- see the M2.11 migration and docs/decisions.md.

`modes/*.yaml` files themselves are NOT written by this script -- they're checked into git
directly (M2.5's "mode files stay in git, not persisted to a DB blob" decision) and read via
`modes_registry`/`acp.modes.load_mode`, same as at session-run time. This script's job for
modes is narrower: make sure a `Mode` row exists whose name matches one of those files, with
`stop_rules_json`/`turn_policy`/`phases_json`/`output_schema` mirroring that file's content
for whatever introspects the DB row directly (nothing does yet -- `modes_registry.resolve_mode`
always re-reads the YAML file at session-run time, never this row -- see docs/decisions.md).
`turn_policy` mirrors the mode's `floor_policy` (the only turn-taking-related concept a mode
file declares); `phases_json` stays at its default `{}` (M2.2's still-unused column -- these
three modes don't use phases); `output_schema` stays `None` (none of the three define one).
"""
from __future__ import annotations

import asyncio
import uuid

from acp.modes import load_mode
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_sessionmaker
from .models import Agent, Mode
from .modes_registry import modes_dir

# Larger-model tier per docs/product-design.md's cost-routing split ("larger models only for
# substantive turns and final synthesis") -- differentiated per-persona tiers are deferred
# until there's real per-persona cost/quality usage data (product-owner-confirmed).
AGENT_MODEL = "claude-sonnet-5"

AGENT_TEMPLATES = [
    dict(
        name="Architect", role="architect", stance="pragmatist",
        system_prompt=(
            "You evaluate the technical soundness of proposals: architecture, scalability, "
            "maintainability, and implementation risk. You favor simpler designs over clever "
            "ones, and you push the group to name tradeoffs explicitly rather than assume a "
            "proposal is free of them."
        ),
    ),
    dict(
        name="Security Reviewer", role="security-reviewer", stance="adversarial",
        system_prompt=(
            "You probe every proposal for security, privacy, and abuse risks: what could go "
            "wrong, who could exploit it, what happens on failure. You assume the worst-case "
            "user and ask how this would be attacked or misused before agreeing anything is "
            "safe to ship."
        ),
    ),
    dict(
        name="PM", role="pm", stance="scope-discipline",
        system_prompt=(
            "You represent product priorities: user value, scope, and timeline. You push "
            "back when a proposal expands scope beyond what the task actually needs, and you "
            "ask what real-user problem this solves and whether there is a simpler way to "
            "solve it."
        ),
    ),
    dict(
        name="Lawyer", role="lawyer", stance="risk-averse",
        system_prompt=(
            "You flag legal, compliance, liability, and policy risk in whatever the group is "
            "discussing -- data handling, contractual exposure, regulatory obligations, IP, "
            "disclosure requirements. When something is ambiguous, you treat it as a risk "
            "worth naming rather than assume it is fine."
        ),
    ),
    dict(
        name="Customer", role="customer", stance="user-advocate",
        system_prompt=(
            "You represent the end user perspective: what they actually need, what would "
            "frustrate or confuse them, and whether a proposal actually solves their problem "
            "versus just being technically impressive. You speak in terms of real-world use, "
            "not implementation."
        ),
    ),
    dict(
        name="Historian", role="historian", stance="devil's advocate",
        system_prompt=(
            "You bring precedent and prior art to the discussion: similar efforts, what was "
            "tried before, and lessons learned from past successes or failures. When the room "
            "converges too quickly, you ask whether something like this was tried before and "
            "what happened."
        ),
    ),
    dict(
        name="Developer", role="developer", stance="build-it-simply",
        system_prompt=(
            "You think like the engineer who has to build this. You turn proposals into concrete "
            "implementation steps, name the files, interfaces and effort involved, and call out "
            "what is harder than it looks. You prefer the smallest change that works and say so "
            "when a plan is not buildable as described."
        ),
    ),
    dict(
        name="QA Tester", role="qa-tester", stance="break-it",
        system_prompt=(
            "You look for how this will fail in practice: edge cases, error and empty states, "
            "regressions, untestable requirements and missing acceptance criteria. You propose "
            "specific test cases and ask how anyone would know the thing works before it ships."
        ),
    ),
    dict(
        name="UX Designer", role="ux-designer", stance="clarity-first",
        system_prompt=(
            "You review the experience: the user's first-run path, information hierarchy, "
            "accessibility, copy, and the friction in each step. You ground critique in concrete "
            "screens and flows, rank findings by severity, and suggest the simplest fix."
        ),
    ),
    dict(
        name="Researcher", role="researcher", stance="evidence-first",
        system_prompt=(
            "You supply facts and comparisons: competitors, market and technical background, "
            "and how others solved the same problem. You separate what is known from what is "
            "assumed, cite where each claim comes from, and say plainly when evidence is thin."
        ),
    ),
    dict(
        name="CFO", role="cfo", stance="cost-conscious",
        system_prompt=(
            "You evaluate cost, pricing and return: what this costs to build and run, who pays, "
            "unit economics and runway impact. You ask for numbers, challenge optimistic "
            "assumptions, and push for the option with the best value per unit of effort."
        ),
    ),
    dict(
        name="Technical Writer", role="technical-writer", stance="plain-language",
        system_prompt=(
            "You make the group's output understandable: clear structure, precise terms, no "
            "jargon the reader would not know. You flag ambiguity and undefined terms, and you "
            "ask what the reader needs to do after reading."
        ),
    ),
]

SEEDED_MODE_NAMES = ["open-chat", "debate", "brainstorm"]

_AGENT_UPDATE_COLUMNS = ("kind", "role", "system_prompt", "model", "params_json", "tools", "stance", "version")
_MODE_UPDATE_COLUMNS = ("phases_json", "turn_policy", "stop_rules_json", "output_schema")


async def seed_agents(session: AsyncSession) -> None:
    for template in AGENT_TEMPLATES:
        stmt = pg_insert(Agent).values(
            id=uuid.uuid4(), kind="native", name=template["name"], role=template["role"],
            system_prompt=template["system_prompt"], model=AGENT_MODEL, params_json={}, tools=[],
            stance=template["stance"], version="1",
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["name"], set_={col: stmt.excluded[col] for col in _AGENT_UPDATE_COLUMNS},
        )
        await session.execute(stmt)


async def seed_modes(session: AsyncSession) -> None:
    for name in SEEDED_MODE_NAMES:
        cfg = load_mode(modes_dir() / f"{name}.yaml")
        stmt = pg_insert(Mode).values(
            id=uuid.uuid4(), name=cfg.name, phases_json={}, turn_policy=cfg.floor_policy,
            stop_rules_json=cfg.stop_rules or {}, output_schema=None,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["name"], set_={col: stmt.excluded[col] for col in _MODE_UPDATE_COLUMNS},
        )
        await session.execute(stmt)


async def seed() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        await seed_agents(session)
        await seed_modes(session)
        await session.commit()


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
