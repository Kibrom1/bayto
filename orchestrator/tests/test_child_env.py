from orchestrator.child_env import child_env


def test_children_get_no_otel_variables_but_keep_the_rest(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4317")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "x")
    monkeypatch.setenv("HOME_MARKER", "kept")
    env = child_env({"ROSTER": "r.txt"})
    assert not any(k.startswith("OTEL_") for k in env)
    assert env["HOME_MARKER"] == "kept" and env["ROSTER"] == "r.txt"


def test_provider_keys_never_reach_a_child_but_addresses_do(monkeypatch):
    from orchestrator.child_env import child_env
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("GEMINI_API_KEY", "g-secret")
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example/v1")
    env = child_env()
    assert "OPENAI_API_KEY" not in env and "GEMINI_API_KEY" not in env
    assert env["OLLAMA_HOST"] and env["OPENAI_BASE_URL"]
    assert child_env({"OPENAI_API_KEY": "explicit"})["OPENAI_API_KEY"] == "explicit"  # a deliberate hand-over still works


def test_git_and_gh_children_get_the_scrubbed_environment(monkeypatch, tmp_path):
    """workspace.py and pull_request.py used to inherit the whole environment, provider keys included."""
    import subprocess
    from orchestrator import pull_request, workspace
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    seen = []
    real = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (seen.append(k.get("env")), real(*a, **k))[1])
    workspace._git(str(tmp_path), "status")
    pull_request._run(["git", "--version"], str(tmp_path))
    assert len(seen) == 2 and all(e is not None and "OPENAI_API_KEY" not in e for e in seen)
    assert seen[1]["GIT_TERMINAL_PROMPT"] == "0"
