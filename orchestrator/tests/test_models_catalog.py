"""Step 1 of docs/per-agent-models.md: the model catalogue module, GET /models, and
422 invalid_model validation for seat models on POST /sessions. The catalogue tests
below need no DB; the POST /sessions tests need `live_sessionmaker` (real Postgres) and
skip cleanly when none is reachable, same as the rest of this suite (see conftest.py).
"""
import uuid
from pathlib import Path

import httpx

from orchestrator import models_catalog
from orchestrator.app import app
from orchestrator.models import Agent, Mode, Session, SessionAgent, Task

FIXTURES = Path(__file__).parent / "fixtures" / "modes"


# ---------------------------------------------------------------- the catalogue module

def test_catalog_has_the_three_builtin_models_with_exactly_one_default():
    cat = models_catalog.catalog()
    by_id = {m.id: m for m in cat}
    assert by_id.keys() == {"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"}
    assert by_id["claude-opus-5-5"].label == "Opus 5.5" and by_id["claude-opus-5-5"].tier == "large"
    assert by_id["claude-sonnet-5-5"].label == "Sonnet 5.5" and by_id["claude-sonnet-5-5"].tier == "standard"
    assert by_id["claude-haiku-5-5"].label == "Haiku 5.5" and by_id["claude-haiku-5-5"].tier == "small"
    assert [m.default for m in cat].count(True) == 1
    assert by_id["claude-sonnet-5-5"].default is True


def test_default_model_id_is_sonnet():
    assert models_catalog.default_model_id() == "claude-sonnet-5-5"


def test_is_known_model_true_for_catalog_ids_and_none():
    assert models_catalog.is_known_model("claude-opus-5-5") is True
    assert models_catalog.is_known_model("claude-sonnet-5-5") is True
    assert models_catalog.is_known_model("claude-haiku-5-5") is True
    assert models_catalog.is_known_model(None) is True


def test_is_known_model_false_for_an_unknown_id():
    assert models_catalog.is_known_model("not-a-real-model") is False


def test_bayto_extra_models_env_var_adds_an_entry(monkeypatch):
    monkeypatch.setenv("BAYTO_EXTRA_MODELS", "local-llama:Local Llama:local:0")
    cat = models_catalog.catalog()
    extra = next(m for m in cat if m.id == "local-llama")
    assert extra.label == "Local Llama" and extra.tier == "local" and extra.usd_per_turn == 0.0
    assert models_catalog.is_known_model("local-llama") is True


def test_bayto_extra_models_env_var_is_read_fresh_each_call(monkeypatch):
    assert models_catalog.is_known_model("gateway-model") is False
    monkeypatch.setenv("BAYTO_EXTRA_MODELS", "gateway-model:Gateway:local:0")
    assert models_catalog.is_known_model("gateway-model") is True
    monkeypatch.delenv("BAYTO_EXTRA_MODELS")
    assert models_catalog.is_known_model("gateway-model") is False


# ---------------------------------------------------------------- GET /models

async def test_get_models_returns_the_catalogue_and_default():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["default"] == "claude-sonnet-5-5"
    ids = {m["id"] for m in body["models"]}
    assert ids == {"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"}
    sonnet = next(m for m in body["models"] if m["id"] == "claude-sonnet-5-5")
    assert sonnet == {"id": "claude-sonnet-5-5", "label": "Sonnet 5.5", "tier": "standard",
                       "usd_per_turn": 0.5, "default": True,
                       "provider": "anthropic", "provider_label": "Anthropic", "ready": True, "needs": None, "build_ok": True}


# ---------------------------------------------------------------- POST /sessions validation

async def _seed(sessionmaker, *, mode_name="round-robin-test", roles=("a", "b")) -> tuple:
    async with sessionmaker() as db:
        task = Task(title="t", output_type="text")
        mode = Mode(name=mode_name, phases_json={}, stop_rules_json={})
        agents = [Agent(kind="native", name=r, role=r) for r in roles]
        db.add_all([task, mode, *agents])
        await db.commit()
        return task.id, mode.id, [a.id for a in agents]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_create_session_accepts_a_known_catalog_model(live_sessionmaker, monkeypatch):
    monkeypatch.setenv("BAYTO_MODES_DIR", str(FIXTURES))
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    async with _client() as client:
        resp = await client.post("/sessions", json={
            "task_id": str(task_id), "mode_id": str(mode_id),
            "roster": [{"agent_id": str(agent_ids[0]), "model": "claude-opus-5-5"},
                       {"agent_id": str(agent_ids[1]), "model": "claude-haiku-5-5"}],
        })
    assert resp.status_code == 201, resp.text
    models = {sa["agent_id"]: sa["model"] for sa in resp.json()["session_agents"]}
    assert models[str(agent_ids[0])] == "claude-opus-5-5"
    assert models[str(agent_ids[1])] == "claude-haiku-5-5"


async def test_create_session_accepts_a_null_or_omitted_model(live_sessionmaker, monkeypatch):
    monkeypatch.setenv("BAYTO_MODES_DIR", str(FIXTURES))
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    async with _client() as client:
        resp = await client.post("/sessions", json={
            "task_id": str(task_id), "mode_id": str(mode_id),
            "roster": [{"agent_id": str(agent_ids[0]), "model": None},
                       {"agent_id": str(agent_ids[1])}],
        })
    assert resp.status_code == 201, resp.text
    models = {sa["agent_id"]: sa["model"] for sa in resp.json()["session_agents"]}
    assert models[str(agent_ids[0])] is None and models[str(agent_ids[1])] is None


async def test_create_session_audits_each_seats_resolved_model(live_sessionmaker, monkeypatch, span_exporter):
    from audit_helpers import audit_events

    monkeypatch.setenv("BAYTO_MODES_DIR", str(FIXTURES))
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    async with _client() as client:
        resp = await client.post("/sessions", json={
            "task_id": str(task_id), "mode_id": str(mode_id),
            "roster": [{"agent_id": str(agent_ids[0]), "model": "claude-opus-5-5"},
                       {"agent_id": str(agent_ids[1]), "model": None}],
        })
    assert resp.status_code == 201, resp.text
    session_id = uuid.UUID(resp.json()["session"]["id"])

    [created] = audit_events(span_exporter, session_id=session_id, action="session.created")
    models = {row["agent_id"]: row["model"] for row in created.detail["roster"]}
    assert models[str(agent_ids[0])] == "claude-opus-5-5"
    assert models[str(agent_ids[1])] is None


async def test_create_session_rejects_an_unknown_model(live_sessionmaker, monkeypatch):
    monkeypatch.setenv("BAYTO_MODES_DIR", str(FIXTURES))
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    async with _client() as client:
        resp = await client.post("/sessions", json={
            "task_id": str(task_id), "mode_id": str(mode_id),
            "roster": [{"agent_id": str(agent_ids[0]), "model": "not-a-real-model"}],
        })
    assert resp.status_code == 422
    error = resp.json()["detail"]["error"]
    assert error["code"] == "invalid_model"
    assert error["field"] == "roster.model"


# ---------------------------------------------------------------- legacy ids still load

async def test_continue_keeps_a_legacy_pre_catalog_model_id(live_sessionmaker, monkeypatch):
    """A SessionAgent.model written before this catalogue existed (seed.py's old
    AGENT_MODEL, "claude-sonnet-5" -- confirmed not one of the three catalog ids) must
    still load: /continue only ever copies the stored value, never validates it."""
    monkeypatch.setenv("BAYTO_MODES_DIR", str(FIXTURES))
    task_id, mode_id, agent_ids = await _seed(live_sessionmaker)
    async with live_sessionmaker() as db:
        session = Session(task_id=task_id, mode_id=mode_id, status="failed")
        db.add(session)
        await db.flush()
        db.add_all([
            SessionAgent(session_id=session.id, agent_id=agent_ids[0], seat_order=0, model="claude-sonnet-5"),
            SessionAgent(session_id=session.id, agent_id=agent_ids[1], seat_order=1, model="claude-sonnet-5"),
        ])
        await db.commit()
        session_id = session.id

    async with _client() as client:
        resp = await client.post(f"/sessions/{session_id}/continue")
    assert resp.status_code == 201, resp.text
    models = {sa["agent_id"]: sa["model"] for sa in resp.json()["session_agents"]}
    assert models[str(agent_ids[0])] == "claude-sonnet-5"
    assert models[str(agent_ids[1])] == "claude-sonnet-5"



def test_models_endpoint_reports_provider_readiness(monkeypatch):
    from fastapi.testclient import TestClient
    from orchestrator.api.model_catalog import router
    from fastapi import FastAPI
    monkeypatch.setenv("BAYTO_EXTRA_MODELS", "openai/gpt-x:GPT X:small:0.05,gemini/g1:G1:small:0.05")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app = FastAPI(); app.include_router(router)
    by_id = {m["id"]: m for m in TestClient(app).get("/models").json()["models"]}
    assert by_id["claude-opus-5-5"]["ready"] and by_id["claude-opus-5-5"]["provider"] == "anthropic"
    assert by_id["openai/gpt-x"]["ready"] and by_id["openai/gpt-x"]["provider_label"] == "OpenAI"
    assert not by_id["gemini/g1"]["ready"] and by_id["gemini/g1"]["needs"] == "GEMINI_API_KEY"
