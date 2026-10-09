"""M2.11: orchestrator.seed's idempotent upsert of the six agent templates and three
discussion modes, against a real Postgres (the natural-key ON CONFLICT ... DO UPDATE this
relies on needs a real unique constraint -- see the M2.11 migration and docs/decisions.md).
No live Anthropic/sbx call anywhere in this module -- pure insert/select round-trips."""
from sqlalchemy import select

from orchestrator.models import Agent, Mode
from orchestrator.seed import AGENT_TEMPLATES, SEEDED_MODE_NAMES, seed_agents, seed_modes


async def test_seed_agents_inserts_all_six_templates_with_the_expected_shape(live_sessionmaker):
    async with live_sessionmaker() as session:
        await seed_agents(session)
        await session.commit()

    async with live_sessionmaker() as session:
        rows = (await session.execute(select(Agent))).scalars().all()

    assert {r.name for r in rows} == {t["name"] for t in AGENT_TEMPLATES}
    assert len(rows) == 13
    by_name = {r.name: r for r in rows}
    architect = by_name["Architect"]
    assert architect.kind == "native"
    assert architect.role == "architect"
    assert architect.stance == "pragmatist"
    assert architect.model == "claude-sonnet-5"
    assert "tradeoffs" in architect.system_prompt
    assert by_name["Historian"].stance == "devil's advocate"


async def test_seed_agents_is_idempotent(live_sessionmaker):
    async with live_sessionmaker() as session:
        await seed_agents(session)
        await session.commit()
    async with live_sessionmaker() as session:
        await seed_agents(session)
        await session.commit()

    async with live_sessionmaker() as session:
        rows = (await session.execute(select(Agent))).scalars().all()
    assert len(rows) == 13  # not 26 -- re-running upserts, never duplicates


async def test_seed_agents_updates_existing_rows_in_place_on_a_content_tweak(live_sessionmaker, monkeypatch):
    async with live_sessionmaker() as session:
        await seed_agents(session)
        await session.commit()
    async with live_sessionmaker() as session:
        original_id = (await session.execute(
            select(Agent.id).where(Agent.name == "Architect")
        )).scalar_one()

    tweaked = [dict(t) for t in AGENT_TEMPLATES]
    tweaked[0] = {**tweaked[0], "system_prompt": "A brand new prompt after a content tweak."}
    monkeypatch.setattr("orchestrator.seed.AGENT_TEMPLATES", tweaked)

    async with live_sessionmaker() as session:
        await seed_agents(session)
        await session.commit()

    async with live_sessionmaker() as session:
        rows = (await session.execute(select(Agent))).scalars().all()
        updated = (await session.execute(
            select(Agent).where(Agent.name == "Architect")
        )).scalar_one()

    assert len(rows) == 13  # the tweak updated the existing row, didn't add a fourteenth
    assert updated.id == original_id  # same row, not a new one under a fresh id
    assert updated.system_prompt == "A brand new prompt after a content tweak."


async def test_seed_modes_inserts_all_three_matching_the_real_yaml_files(live_sessionmaker):
    async with live_sessionmaker() as session:
        await seed_modes(session)
        await session.commit()

    async with live_sessionmaker() as session:
        rows = (await session.execute(select(Mode))).scalars().all()

    assert {r.name for r in rows} == set(SEEDED_MODE_NAMES)
    by_name = {r.name: r for r in rows}
    assert by_name["open-chat"].turn_policy == "raise-hand"
    assert by_name["open-chat"].stop_rules_json["max_rounds"] is None
    assert by_name["debate"].turn_policy == "round-robin"
    assert by_name["debate"].stop_rules_json["max_rounds"] == 6
    assert by_name["brainstorm"].stop_rules_json["max_rounds"] == 6
    assert all(r.phases_json == {} for r in rows)
    assert all(r.output_schema is None for r in rows)


async def test_seed_modes_is_idempotent(live_sessionmaker):
    async with live_sessionmaker() as session:
        await seed_modes(session)
        await session.commit()
    async with live_sessionmaker() as session:
        await seed_modes(session)
        await session.commit()

    async with live_sessionmaker() as session:
        rows = (await session.execute(select(Mode))).scalars().all()
    assert len(rows) == 3


def test_templates_cover_the_core_team_and_are_unique():
    names = [t["name"] for t in AGENT_TEMPLATES]
    assert len(names) == len(set(names)) == 13
    assert {"Developer", "QA Tester", "E2E Tester", "UX Designer", "Researcher", "CFO", "Technical Writer"} <= set(names)
    assert all(t["system_prompt"].strip() and t["stance"] and t["role"] for t in AGENT_TEMPLATES)
