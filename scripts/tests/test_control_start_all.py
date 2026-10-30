"""start_all(): dependency order, idempotence, and failure reporting (no Docker or processes needed)."""
import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location("bayto_control", pathlib.Path(__file__).parents[1] / "bayto-control.py")
bc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bc)


class FakeProc:
    def __init__(self, running=False):
        self._running, self.log, self.started = running, [], 0

    def running(self):
        return self._running

    def start(self):
        self.started += 1
        self._running = True
        return "starting"


def patch(monkeypatch, pg="stopped", obs="stopped", orch=False, web=False, healthy=True):
    calls = []
    monkeypatch.setattr(bc, "pg_status", lambda: pg)
    monkeypatch.setattr(bc, "obs_status", lambda: obs)
    monkeypatch.setattr(bc, "pg_start", lambda: calls.append("postgres") or "started")
    monkeypatch.setattr(bc, "obs_start", lambda: calls.append("observability") or "started")
    monkeypatch.setattr(bc, "ORCH_PROC", FakeProc(orch))
    monkeypatch.setattr(bc, "WEB_PROC", FakeProc(web))
    monkeypatch.setattr(bc, "port_open", lambda *a, **k: True)
    monkeypatch.setattr(bc, "orch_call", lambda *a, **k: (200 if healthy else 0, {}))
    monkeypatch.setattr(bc, "run", lambda *a, **k: (0, "pg line 1\npg line 2"))
    return calls


def ready(check, timeout, every=1.0):
    return check()


def test_starts_everything_in_order(monkeypatch):
    # pg/obs report stopped until started: emulate by flipping status after start
    state = {"pg": "stopped", "obs": "stopped"}
    calls = patch(monkeypatch)
    monkeypatch.setattr(bc, "pg_status", lambda: state["pg"])
    monkeypatch.setattr(bc, "obs_status", lambda: state["obs"])
    monkeypatch.setattr(bc, "pg_start", lambda: state.update(pg="running") or calls.append("postgres") or "started")
    monkeypatch.setattr(bc, "obs_start", lambda: state.update(obs="running") or calls.append("observability") or "started")
    r = bc.start_all(waiter=ready)
    assert r["ok"] and [s["name"] for s in r["steps"]] == ["postgres", "observability", "orchestrator", "web"]
    assert calls == ["postgres", "observability"] and bc.ORCH_PROC.started == 1 and bc.WEB_PROC.started == 1


def test_is_idempotent_when_everything_runs(monkeypatch):
    calls = patch(monkeypatch, pg="running", obs="running", orch=True, web=True)
    r = bc.start_all(waiter=ready)
    assert r["ok"] and all(s["detail"] == "already running" for s in r["steps"])
    assert calls == [] and bc.ORCH_PROC.started == 0


def test_stops_at_the_first_failure_with_its_log(monkeypatch):
    patch(monkeypatch, pg="running", obs="running", healthy=False)
    bc.ORCH_PROC.log = ["boot", "Traceback: boom"]
    r = bc.start_all(waiter=ready)
    assert not r["ok"] and r["failed"] == "orchestrator"
    assert r["log"] == ["boot", "Traceback: boom"]
    assert [s["name"] for s in r["steps"]] == ["postgres", "observability", "orchestrator"]  # web never attempted
    assert bc.WEB_PROC.started == 0


def test_session_stop_sends_a_body_and_skips_the_write_up(monkeypatch):
    calls = []
    monkeypatch.setattr(bc, "orch_call", lambda m, p, b=None: calls.append((m, p, b)) or (202, {}))
    sid = "05822fd7-2e46-4ab2-975b-87d3b55997f8"
    bc.do_action({"action": "session", "id": sid, "op": "stop"})
    bc.do_action({"action": "session", "id": sid, "op": "pause"})
    assert calls[0] == ("POST", f"/sessions/{sid}/stop", {"synthesize": False})
    assert calls[1] == ("POST", f"/sessions/{sid}/pause", None)
