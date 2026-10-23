from orchestrator.child_env import child_env


def test_children_get_no_otel_variables_but_keep_the_rest(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4317")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "x")
    monkeypatch.setenv("HOME_MARKER", "kept")
    env = child_env({"ROSTER": "r.txt"})
    assert not any(k.startswith("OTEL_") for k in env)
    assert env["HOME_MARKER"] == "kept" and env["ROSTER"] == "r.txt"
