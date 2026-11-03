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


def _git(cwd, *args):
    import subprocess
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def test_sync_main_moves_the_repo_to_the_latest_main(tmp_path):
    import subprocess
    origin, work, other = tmp_path / "o.git", tmp_path / "w", tmp_path / "x"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    (work / "a").write_text("1")
    _git(work, "add", "a"); _git(work, "commit", "-qm", "one"); _git(work, "push", "-q", "origin", "main")
    _git(work, "checkout", "-qb", "bayto/build-old")
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    (other / "b").write_text("2")
    _git(other, "add", "b"); _git(other, "commit", "-qm", "two"); _git(other, "push", "-q", "origin", "main")
    assert rl.sync_main(str(work)) == ""
    assert _git(work, "branch", "--show-current") == "main" and (work / "b").exists()


def test_sync_main_refuses_a_dirty_tree_and_changes_nothing(tmp_path):
    import subprocess
    origin, work = tmp_path / "o.git", tmp_path / "w"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    (work / "a").write_text("1")
    _git(work, "add", "a"); _git(work, "commit", "-qm", "one"); _git(work, "push", "-q", "origin", "main")
    _git(work, "checkout", "-qb", "bayto/build-old")
    (work / "a").write_text("changed on the branch")
    _git(work, "commit", "-qam", "edit on branch")
    (work / "a").write_text("uncommitted")  # would be overwritten by checking out main
    assert "failed" in rl.sync_main(str(work))
    assert _git(work, "branch", "--show-current") == "bayto/build-old"
    assert rl.sync_main(None) == ""
