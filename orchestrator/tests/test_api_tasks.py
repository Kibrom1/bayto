"""M2.8: POST /tasks. Real Postgres (task rows), no sbx or Anthropic needed."""
import httpx

from orchestrator.app import app


async def test_create_task_persists_and_returns_it(live_sessionmaker):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/tasks", json={
            "title": "Evaluate vendor X", "brief": "Compare X vs Y on cost and latency",
            "output_type": "memo",
        })

    assert resp.status_code == 201
    body = resp.json()["task"]
    assert body["title"] == "Evaluate vendor X"
    assert body["success_criteria"] is None
    assert body["owner_id"] is None
    assert body["created_at"]  # a real timestamp was stamped server-side


async def test_create_task_accepts_optional_fields(live_sessionmaker):
    owner_id = "11111111-1111-1111-1111-111111111111"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/tasks", json={
            "title": "t", "brief": "b", "output_type": "text",
            "success_criteria": "ships a working demo", "owner_id": owner_id,
        })

    assert resp.status_code == 201
    body = resp.json()["task"]
    assert body["success_criteria"] == "ships a working demo"
    assert body["owner_id"] == owner_id


async def test_create_task_rejects_a_missing_required_field(live_sessionmaker):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/tasks", json={"title": "t", "brief": "b"})  # missing output_type

    assert resp.status_code == 422
