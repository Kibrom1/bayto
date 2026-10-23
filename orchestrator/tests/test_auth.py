"""BAYTO_API_TOKEN: optional shared-secret auth. Open when unset; 401/200 matrix, cookie + bearer, SSE, constant-time compare."""
import uuid

import pytest
from fastapi.testclient import TestClient

from orchestrator import auth
from orchestrator.app import app
from orchestrator.child_env import child_env

TOKEN = "s3cret-token-for-tests"
EXEMPT = [("GET", "/healthz"), ("GET", "/app-config")]
PROTECTED = [("GET", "/tasks"), ("GET", "/models"), ("POST", "/tasks"), ("DELETE", f"/sessions/{uuid.uuid4()}"),
             ("GET", f"/sessions/{uuid.uuid4()}/events")]


@pytest.fixture(autouse=True)
def _reset():
    auth._failures.clear()
    yield
    auth._failures.clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_open_when_unset_or_empty(client, monkeypatch):
    for value in (None, ""):
        if value is None:
            monkeypatch.delenv("BAYTO_API_TOKEN", raising=False)
        else:
            monkeypatch.setenv("BAYTO_API_TOKEN", value)
        assert client.get("/app-config").json()["auth_required"] is False
        assert client.get("/app-config").json()["authenticated"] is True
        assert client.get("/models").status_code != 401
        assert client.post("/auth/login", json={"token": "x"}).status_code == 404


def test_matrix_without_credentials(client, monkeypatch):
    monkeypatch.setenv("BAYTO_API_TOKEN", TOKEN)
    for method, path in EXEMPT:
        assert client.request(method, path).status_code == 200
    for method, path in PROTECTED:
        r = client.request(method, path)
        assert r.status_code == 401, (method, path)
        assert r.json() == {"detail": {"error": {"code": "unauthorized", "detail": "authentication required"}}}
        assert r.headers["www-authenticate"] == "Bearer"
    cfg = client.get("/app-config").json()
    assert cfg["auth_required"] is True and cfg["authenticated"] is False


def test_bearer_token(client, monkeypatch):
    monkeypatch.setenv("BAYTO_API_TOKEN", TOKEN)
    assert client.get("/models", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert client.get("/models", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/models", headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401
    assert client.get("/app-config", headers={"Authorization": f"Bearer {TOKEN}"}).json()["authenticated"] is True


def test_login_sets_cookie_that_works_and_logout_clears_it(client, monkeypatch):
    monkeypatch.setenv("BAYTO_API_TOKEN", TOKEN)
    r = client.post("/auth/login", json={"token": TOKEN})
    assert r.status_code == 200
    sc = r.headers["set-cookie"].lower()
    assert "bayto_session=" in sc and "httponly" in sc and "samesite=strict" in sc and TOKEN not in r.headers["set-cookie"]
    assert client.get("/models").status_code == 200
    assert client.get("/app-config").json()["authenticated"] is True
    assert client.post("/auth/logout").status_code == 200
    assert client.get("/models").status_code == 401


def test_cookie_is_secure_behind_https_proxy(client, monkeypatch):
    monkeypatch.setenv("BAYTO_API_TOKEN", TOKEN)
    r = client.post("/auth/login", json={"token": TOKEN}, headers={"X-Forwarded-Proto": "https"})
    assert "secure" in r.headers["set-cookie"].lower()


def test_forged_cookie_and_raw_token_cookie_are_refused(client, monkeypatch):
    monkeypatch.setenv("BAYTO_API_TOKEN", TOKEN)
    assert client.get("/models", cookies={"bayto_session": "nope"}).status_code == 401
    assert client.get("/models", cookies={"bayto_session": TOKEN}).status_code == 401


def test_cookie_stops_working_when_token_changes(client, monkeypatch):
    monkeypatch.setenv("BAYTO_API_TOKEN", TOKEN)
    client.post("/auth/login", json={"token": TOKEN})
    monkeypatch.setenv("BAYTO_API_TOKEN", "rotated")
    assert client.get("/models").status_code == 401


def test_sse_accepts_the_cookie(client, monkeypatch):
    """Without credentials the stream is refused; with the cookie the request gets past auth (a malformed id then answers 422, not 401)."""
    monkeypatch.setenv("BAYTO_API_TOKEN", TOKEN)
    path = "/sessions/not-a-uuid/events"  # 422 on the id, before any database access
    assert client.get(path).status_code == 401
    client.post("/auth/login", json={"token": TOKEN})
    assert client.get(path).status_code not in (401, 403)


def test_wrong_login_is_401_then_rate_limited_and_recovers(client, monkeypatch):
    monkeypatch.setenv("BAYTO_API_TOKEN", TOKEN)
    for _ in range(auth._FREE_FAILURES):
        r = client.post("/auth/login", json={"token": "bad"})
        assert r.status_code == 401 and r.json()["detail"]["error"]["code"] == "invalid_token"
        assert "set-cookie" not in r.headers
    r = client.post("/auth/login", json={"token": "bad"})  # the next failure starts a lock-out
    assert r.status_code == 401
    r = client.post("/auth/login", json={"token": TOKEN})  # even the right token waits
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    auth._failures.clear()  # stand-in for the wait
    assert client.post("/auth/login", json={"token": TOKEN}).status_code == 200


def test_constant_time_compare_is_used(client, monkeypatch):
    monkeypatch.setenv("BAYTO_API_TOKEN", TOKEN)
    calls = []
    real = auth.hmac.compare_digest
    monkeypatch.setattr(auth.hmac, "compare_digest", lambda a, b: (calls.append((type(a), type(b))), real(a, b))[1])
    client.post("/auth/login", json={"token": "bad"})
    client.get("/models", headers={"Authorization": "Bearer bad"})
    client.get("/models", cookies={"bayto_session": "bad"})
    assert len(calls) >= 3
    src = open(auth.__file__).read()
    assert "== token" not in src and "token ==" not in src


def test_token_is_not_in_child_env(monkeypatch):
    monkeypatch.setenv("BAYTO_API_TOKEN", TOKEN)
    assert "BAYTO_API_TOKEN" not in child_env()
