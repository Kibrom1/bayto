"""M3.1: GET /agents/templates. Filtered to kind="native" -- the reusable M2.11 personas,
distinct from external/remote agents. Real Postgres, no sbx or Anthropic needed."""
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
        "model": "claude-sonnet-5",
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
