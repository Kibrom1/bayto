"""M3.1: GET /modes. Reads modes/<name>.yaml live via resolve_mode(), not Mode.stop_rules_json
(see api/modes.py's module docstring). Real Postgres for the Mode row itself, BAYTO_MODES_DIR
pointed at fixture files, no sbx or Anthropic needed."""
from pathlib import Path

import httpx

from orchestrator.app import app

from test_api_tasks import _make_mode

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "modes"


async def test_list_modes_returns_the_parsed_yaml_content(live_sessionmaker, monkeypatch):
    monkeypatch.setenv("BAYTO_MODES_DIR", str(FIXTURES_DIR))
    mode = await _make_mode(live_sessionmaker, "round-robin-test")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/modes")

    assert resp.status_code == 200
    [body] = [m for m in resp.json() if m["id"] == str(mode.id)]
    assert body["name"] == "round-robin-test"
    assert body["floor_policy"] == "round-robin"
    # round-robin-test.yaml only sets max_rounds -- everything else falls back to
    # StopRulesConfig's own product-owner-confirmed defaults (M2.7).
    assert body["stop_rules"] == {
        "max_rounds": 2, "converge_after_quiet_rounds": 2, "stale_argument_turns": 3,
        "urgency_boost_multiplier": 1.3, "max_consecutive_grants": 2,
        "urgency_boost_reasons": ["new_point", "disagree"],
    }


async def test_list_modes_skips_a_mode_whose_yaml_file_is_missing(live_sessionmaker, monkeypatch):
    """Same failure-isolation principle as M2.9's reconcile routine: one broken/missing
    mode file must not 500 the whole list for every other mode."""
    monkeypatch.setenv("BAYTO_MODES_DIR", str(FIXTURES_DIR))
    good = await _make_mode(live_sessionmaker, "round-robin-test")
    await _make_mode(live_sessionmaker, "no-such-mode-file")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/modes")

    assert resp.status_code == 200
    ids = {m["id"] for m in resp.json()}
    assert str(good.id) in ids
    assert len(resp.json()) == 1


async def test_list_modes_skips_a_mode_with_broken_yaml(live_sessionmaker, monkeypatch, tmp_path):
    (tmp_path / "broken.yaml").write_text(": not: valid: yaml: [")
    (tmp_path / "ok.yaml").write_text("mode: ok\nfloor_policy: round-robin\nroles: {}\n")
    monkeypatch.setenv("BAYTO_MODES_DIR", str(tmp_path))
    await _make_mode(live_sessionmaker, "broken")
    ok = await _make_mode(live_sessionmaker, "ok")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/modes")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == str(ok.id)
