"""M3.1: GET /agents/templates. Filtered to kind="native" -- the reusable M2.11 personas,
distinct from external/remote agents. Real Postgres, no sbx or Anthropic needed."""
import uuid

import httpx

from orchestrator.app import app
from orchestrator.models import Agent


async def test_list_agent_templates_returns_native_agents_with_the_expected_shape(live_sessionmaker):
    async with live_sessionmaker() as db:
        row = Agent(kind="native", name="Architect", role="architect",
                     system_prompt="You evaluate technical soundness.", stance="pragmatist",
                     model="claude-sonnet-5")
        db.add(row)
        await db.commit()
        await db.refresh(row)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/agents/templates")

    assert resp.status_code == 200
    [body] = [a for a in resp.json() if a["id"] == str(row.id)]
    assert body == {
        "id": str(row.id), "name": "Architect", "role": "architect",
        "system_prompt": "You evaluate technical soundness.", "stance": "pragmatist",
        "model": "claude-sonnet-5", "kind": "native", "custom": False, "preset": None,
    }


async def test_list_agent_templates_excludes_non_native_agents(live_sessionmaker):
    async with live_sessionmaker() as db:
        native = Agent(kind="native", name="Lawyer", role="lawyer")
        remote = Agent(kind="remote", name="External Reviewer", role="external-reviewer",
                        protocol="a2a", endpoint="https://example.invalid/agent")
        db.add_all([native, remote])
        await db.commit()
        await db.refresh(native)
        await db.refresh(remote)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/agents/templates")

    ids = {a["id"] for a in resp.json()}
    assert str(native.id) in ids
    assert str(remote.id) not in ids


# ---------------------------------------------------------------- custom agents

def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


NEW = {"name": "Compliance Officer", "stance": "risk-averse", "instructions": "You check every plan against the rules."}


async def test_create_custom_agent_gets_a_role_a_preset_and_no_extra_tools(live_sessionmaker):
    async with _client() as c:
        resp = await c.post("/agents", json=NEW)
    assert resp.status_code == 201
    body = resp.json()
    assert body["role"] == "compliance-officer" and body["kind"] == "custom" and body["custom"] is True
    assert body["preset"] == "discussion" and body["system_prompt"] == NEW["instructions"]
    async with live_sessionmaker() as db:
        row = await db.get(Agent, uuid.UUID(body["id"]))
    assert row.tools == [] and row.kind == "custom" and row.version == "1"


async def test_custom_agent_appears_in_the_catalogue_beside_the_built_ins(live_sessionmaker):
    async with _client() as c:
        made = (await c.post("/agents", json={**NEW, "name": "Data Engineer"})).json()
        listed = (await c.get("/agents/templates")).json()
    assert made["id"] in {a["id"] for a in listed}


async def test_client_cannot_choose_tools_or_an_unknown_preset(live_sessionmaker):
    async with _client() as c:
        bad = await c.post("/agents", json={**NEW, "name": "Sneaky", "preset": "build-developer"})
        extra = await c.post("/agents", json={**NEW, "name": "Sneaky Two", "tools": ["Bash"]})
    assert bad.status_code == 422
    assert extra.status_code == 201  # unknown fields are ignored, never stored
    async with live_sessionmaker() as db:
        row = await db.get(Agent, uuid.UUID(extra.json()["id"]))
    assert row.tools == []


async def test_reserved_and_duplicate_names_are_refused(live_sessionmaker):
    async with _client() as c:
        for name in ("Moderator", "Build Developer", "human"):
            r = await c.post("/agents", json={**NEW, "name": name})
            assert r.status_code == 422, name
        assert (await c.post("/agents", json={**NEW, "name": "Auditor"})).status_code == 201
        assert (await c.post("/agents", json={**NEW, "name": "auditor"})).status_code == 409
        assert (await c.post("/agents", json={**NEW, "name": "A"})).status_code == 422


async def test_validation_rejects_empty_and_oversized_fields(live_sessionmaker):
    async with _client() as c:
        assert (await c.post("/agents", json={**NEW, "name": "  "})).status_code == 422
        assert (await c.post("/agents", json={**NEW, "name": "Long", "instructions": "x" * 4001})).status_code == 422
        assert (await c.post("/agents", json={**NEW, "name": "Nul", "stance": "a\x00b"})).status_code == 422


async def test_update_changes_the_prompt_and_bumps_the_version_but_keeps_the_role(live_sessionmaker):
    async with _client() as c:
        made = (await c.post("/agents", json={**NEW, "name": "Editor"})).json()
        resp = await c.patch(f"/agents/{made['id']}", json={"name": "Chief Editor", "instructions": "Cut ruthlessly."})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Chief Editor" and body["role"] == "editor" and body["system_prompt"] == "Cut ruthlessly."
    async with live_sessionmaker() as db:
        assert (await db.get(Agent, uuid.UUID(made["id"]))).version == "2"


async def test_built_in_agents_cannot_be_edited_or_archived(live_sessionmaker):
    async with live_sessionmaker() as db:
        row = Agent(kind="native", name="Builtin Test", role="builtin-test")
        db.add(row)
        await db.commit()
        await db.refresh(row)
    async with _client() as c:
        assert (await c.patch(f"/agents/{row.id}", json={"stance": "x"})).status_code == 409
        assert (await c.delete(f"/agents/{row.id}")).status_code == 409


async def test_archive_hides_the_agent_keeps_the_row_and_blocks_new_sessions(live_sessionmaker):
    async with _client() as c:
        made = (await c.post("/agents", json={**NEW, "name": "Temp Agent"})).json()
        assert (await c.delete(f"/agents/{made['id']}")).status_code == 204
        listed = (await c.get("/agents/templates")).json()
        again = await c.delete(f"/agents/{made['id']}")
    assert made["id"] not in {a["id"] for a in listed}
    assert again.status_code == 404
    async with live_sessionmaker() as db:
        assert (await db.get(Agent, uuid.UUID(made["id"]))).archived_at is not None


async def test_presets_endpoint_lists_what_a_custom_agent_may_be_given(live_sessionmaker):
    async with _client() as c:
        presets = (await c.get("/agents/presets")).json()
    assert [p["id"] for p in presets] == ["discussion"]
