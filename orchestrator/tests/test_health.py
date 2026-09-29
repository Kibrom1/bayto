from fastapi.testclient import TestClient

from orchestrator.app import app


def test_healthz_ok():
    with TestClient(app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
