import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location("rl", pathlib.Path(__file__).parents[1] / "readiness-loop.py")
rl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rl)


def test_decide():
    assert rl.decide("finished", 0, 0) == "next_round"
    assert rl.decide("failed", 0, 0) == "failed"
    assert rl.decide("active", 0, 0) == "wait"
    assert rl.decide("needs_human", 60, 0) == "wait"                       # give auto-resume time after a limit
    assert rl.decide("needs_human", rl.PAUSE_GRACE + 1, 0) == "nudge"
    assert rl.decide("needs_human", rl.PAUSE_GRACE + 1, rl.MAX_NUDGES) == "end"


def test_new_session_copies_team_models_and_budget(monkeypatch):
    calls = []

    def fake(api, method, path, body=None):
        calls.append((method, path, body))
        return (201, {"session": {"id": "abcdef12"}}) if method == "POST" and path == "/sessions" else (202, {})

    monkeypatch.setattr(rl, "call", fake)
    template = {"session": {"task_id": "t", "mode_id": "m", "budget": {"max_cost": 15.0},
                            "turn_counts": [{"agent_id": "a1", "model": "claude-opus-5-5"},
                                            {"agent_id": "a2", "model": None, "muted": True}]}}
    assert rl.new_session("http://x", template) == "abcdef12"
    body = calls[0][2]
    assert body["roster"] == [{"agent_id": "a1", "seat_order": 0, "model": "claude-opus-5-5", "muted": False},
                              {"agent_id": "a2", "seat_order": 1, "muted": True}]
    assert body["budget"] == {"dollars": 15.0} and calls[1][1] == "/sessions/abcdef12/start"
