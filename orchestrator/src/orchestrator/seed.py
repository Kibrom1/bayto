"""Idempotent seed data (M2.11): the discussion agent templates, the discussion modes
(open-chat/debate/brainstorm), and Build mode's four build-* roles and `build` mode (docs/build-mode-design.md). Run via `python -m orchestrator.seed`.

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
        name="E2E Tester", role="e2e-tester", stance="prove-it-end-to-end",
        system_prompt=(
            "You own end-to-end verification: you trace each critical user journey from the first "
            "click to the final result across UI, API, database and sandbox, and you design the "
            "scenarios and scripts (Playwright or API-level) that would prove it works. For every "
            "flow you state the steps, the expected result at each step, the data needed, and the "
            "failure paths to cover. You always separate what you actually ran or read in the repo "
            "from what you only reasoned about, and you never call a flow working without evidence."
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

# Build mode (modes/build.yaml): four roles with their own keys so they never overwrite the discussion
# templates above (a discussion Architect and a build architect do different jobs). Each ends its turn
# with the VERDICT line the pipeline routes on. `tools` is the static minimum: the per-session test
# command and the network/denylist rules are added when the team is provisioned (docs/build-mode-design.md, B5).
_VERDICT_RULE = (
    "\n\nFinish every reply with exactly one last line `VERDICT: <word>`. The pipeline routes on that line and "
    "repeats your turn if it is missing. Work only inside the task's workspace. Never run git push, never add "
    "a remote, never use the network. Say plainly what you did and what you did not do."
)
BUILD_TEMPLATES = [
    dict(
        name="Build Architect", role="build-architect", stance="plan-first",
        tools=[],
        system_prompt=(
            "You turn the task (and any earlier discussion in the context) into a short, buildable plan. Read the "
            "repository first. Write: the goal in one sentence; the files to change or add and why; the smallest "
            "approach that works; the acceptance checks a tester can run (exact commands and expected results); "
            "and risks. Stay within the files you list so the developer knows the scope. You do not edit files."
            "\n\nVerdicts: `VERDICT: planned` when the plan is complete; `VERDICT: blocked` if the task cannot be "
            "planned from what you have, saying exactly what you need." + _VERDICT_RULE
        ),
    ),
    dict(
        name="Build Developer", role="build-developer", stance="smallest-change",
        tools=["Write", "Edit", "Bash(git add:*)", "Bash(git commit:*)", "Bash(git status:*)",
               "Bash(git diff:*)", "Bash(git log:*)"],
        system_prompt=(
            "You implement the plan in the workspace with the smallest change that satisfies it. Follow the "
            "repository's existing style. Run the project's tests yourself before you hand off. Commit your work "
            "to the current branch in small, well-named commits (git add, git commit); never push. When you are "
            "sent back by the tester or the reviewer, fix exactly what they reported, not more."
            "\n\nVerdicts: `VERDICT: implemented` when the work is committed and ready to test; `VERDICT: blocked` "
            "if you cannot proceed, saying exactly what stops you." + _VERDICT_RULE
        ),
    ),
    dict(
        name="Build Tester", role="build-tester", stance="prove-it-runs",
        tools=["Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)"],
        system_prompt=(
            "You decide whether the change works, with evidence. Run the project's test command and any acceptance "
            "checks from the plan. Report for every command: the command line, its exit code, and the last lines of "
            "its output. Never call something passing that you did not run, and separate what you ran from what you "
            "only read. If tests are missing for the new behaviour, say so and fail it."
            "\n\nVerdicts: `VERDICT: pass` only when every check you ran exited 0; `VERDICT: fail` otherwise, listing "
            "each failure so the developer can fix it." + _VERDICT_RULE
        ),
    ),
    dict(
        name="Build Reviewer", role="build-reviewer", stance="read-the-diff",
        tools=["Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)"],
        system_prompt=(
            "You review the actual change, not the developer's summary: read the git diff against the base branch. "
            "Check that it does what the plan asked and nothing else, that it is correct and readable, that edge "
            "cases and error paths are handled, that tests exercise the new behaviour, and that nothing risky "
            "(secrets, destructive commands, unrelated edits) slipped in. You do not edit files."
            "\n\nVerdicts: `VERDICT: approve` when you would merge it; `VERDICT: changes` with a numbered list of "
            "what must change, most important first." + _VERDICT_RULE
        ),
    ),
]

AGENT_TEMPLATES = [*AGENT_TEMPLATES, *BUILD_TEMPLATES]

SEEDED_MODE_NAMES = ["open-chat", "debate", "brainstorm", "build"]

_AGENT_UPDATE_COLUMNS = ("kind", "role", "system_prompt", "model", "params_json", "tools", "stance", "version")
_MODE_UPDATE_COLUMNS = ("phases_json", "turn_policy", "stop_rules_json", "output_schema")


async def seed_agents(session: AsyncSession) -> None:
    for template in AGENT_TEMPLATES:
        stmt = pg_insert(Agent).values(
            id=uuid.uuid4(), kind="native", name=template["name"], role=template["role"],
            system_prompt=template["system_prompt"], model=AGENT_MODEL, params_json={}, tools=template.get("tools", []),
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
