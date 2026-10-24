"""The task setting for the local fallback: stored value, validation through the catalogue and provider check."""
import uuid

import httpx
import pytest

from orchestrator.app import app

BASE = {"title": "T", "brief": "B", "output_type": "decision"}


@pytest.fixture
def ollama(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    monkeypatch.setenv("BAYTO_EXTRA_MODELS", "ollama/llama3:Llama 3:small:0,ollama/qwen3:Qwen 3:small:0,openai/gpt-x:GPT X:small:0.1")
    monkeypatch.delenv("BAYTO_LOCAL_FALLBACK_MODEL", raising=False)


async def test_it_is_off_by_default_and_reports_readiness(live_sessionmaker, ollama):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        task = (await c.post("/tasks", json=BASE)).json()["task"]
        assert task["local_fallback"] is None
        got = (await c.get(f"/tasks/{task['id']}/local-fallback")).json()
        assert got["enabled"] is False and got["effective_model"] is None and got["build"] is False


async def test_enabling_needs_a_usable_local_model(live_sessionmaker, ollama, monkeypatch):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        tid = (await c.post("/tasks", json=BASE)).json()["task"]["id"]
        url = f"/tasks/{tid}/local-fallback"
        r = await c.put(url, json={"enabled": True})  # no model and no env default
        assert r.status_code == 422 and r.json()["detail"]["error"]["code"] == "no_model"
        assert (await c.put(url, json={"enabled": True, "model": "openai/gpt-x"})).status_code == 422  # not Ollama
        assert (await c.put(url, json={"enabled": True, "model": "claude-opus-5-5"})).status_code == 422
        assert (await c.put(url, json={"enabled": True, "model": "ollama/unknown"})).status_code == 422  # not in the catalogue
        monkeypatch.delenv("OLLAMA_HOST")
        r = await c.put(url, json={"enabled": True, "model": "ollama/llama3"})
        assert r.status_code == 422 and r.json()["detail"]["error"]["code"] == "provider_not_configured"
        monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11434")
        ok = (await c.put(url, json={"enabled": True, "model": "ollama/llama3"})).json()
        assert ok["enabled"] is True and ok["effective_model"] == "ollama/llama3" and ok["ready"] is True
        off = (await c.put(url, json={"enabled": False, "model": "ollama/llama3"})).json()
        assert off["enabled"] is False and off["model"] == "ollama/llama3"
        assert (await c.put(f"/tasks/{uuid.uuid4()}/local-fallback", json={"enabled": False})).status_code == 404


async def test_the_environment_default_model_is_used_when_none_is_chosen(live_sessionmaker, ollama, monkeypatch):
    monkeypatch.setenv("BAYTO_LOCAL_FALLBACK_MODEL", "ollama/qwen3")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        task = (await c.post("/tasks", json={**BASE, "local_fallback": {"enabled": True}})).json()["task"]
        assert task["local_fallback"] == {"enabled": True, "model": None}
        got = (await c.get(f"/tasks/{task['id']}/local-fallback")).json()
        assert got["effective_model"] == "ollama/qwen3" and got["default_model"] == "ollama/qwen3" and got["ready"] is True
        bad = await c.post("/tasks", json={**BASE, "local_fallback": {"enabled": True, "model": "openai/gpt-x"}})
        assert bad.status_code == 422


async def test_a_build_task_stores_the_setting_but_says_it_is_ignored(live_sessionmaker, ollama, tmp_path):
    from orchestrator.models import Task
    async with live_sessionmaker() as s:
        t = Task(title="b", output_type="code change", workspace={"repo_path": str(tmp_path), "base_branch": "main"})
        s.add(t)
        await s.commit()
        tid = t.id
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        got = (await c.put(f"/tasks/{tid}/local-fallback", json={"enabled": True, "model": "ollama/llama3"})).json()
        assert got["enabled"] is True and got["build"] is True
