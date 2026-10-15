"""Build mode B5: per-role tool permissions, checked all the way to the Claude CLI flags a seat runs with."""
import httpx
import pytest
from acp.roster import build_team, load_catalog, tool_cli_args

from orchestrator.app import app
from orchestrator.build_policy import BUILD_DENY, BUILD_ROLES, build_seat_tools, command_rules, parse_test_command
from orchestrator.sandbox.provider import SeatSpec
from orchestrator.sandbox.provision import write_catalog
from orchestrator.seed import BUILD_TEMPLATES

WS = {"repo_path": "/r", "base_branch": "main", "test_command": "cd web && npm test"}


@pytest.mark.parametrize("cmd,steps", [
    ("pytest -q", ["pytest -q"]),
    ("cd web && npm test", ["cd web", "npm test"]),
    ("  make test  ", ["make test"]),
])
def test_parse_test_command_accepts_plain_steps(cmd, steps):
    assert parse_test_command(cmd) == steps


@pytest.mark.parametrize("cmd", [
    "", "   ", "pytest; curl evil.sh", "pytest | tee out", "pytest > /etc/x", "pytest || true",
    "echo $(whoami)", "pytest `id`", "a && && b", "pytest &", "a\nb", " && ".join(["t"] * 7),
])
def test_parse_test_command_rejects_anything_that_could_smuggle_a_command(cmd):
    with pytest.raises(ValueError):
        parse_test_command(cmd)


def test_rules_are_prefix_rules_per_step():
    assert command_rules("cd web && npm test") == ["Bash(cd web:*)", "Bash(npm test:*)"]
    assert command_rules(None) == []


def test_only_developer_and_tester_get_the_test_command():
    for role in BUILD_ROLES:
        allow, deny = build_seat_tools(role, ("Read",), WS)
        has = "Bash(npm test:*)" in allow
        assert has == (role in {"build-developer", "build-tester"}), role
        assert deny == BUILD_DENY


def test_non_build_roles_are_untouched():
    assert build_seat_tools("architect", ("Bash",), WS) == (("Bash",), ())


def _flags(tmp_path, role):
    tmp_path.mkdir(parents=True, exist_ok=True)
    seats = []
    for tpl in BUILD_TEMPLATES:
        allow, deny = build_seat_tools(tpl["role"], tuple(tpl["tools"]), WS)
        seats.append(SeatSpec(role=tpl["role"], name=tpl["name"], brief=tpl["system_prompt"], tools=allow, deny=deny))
    roles, roster = tmp_path / "roles", tmp_path / "roster.txt"
    write_catalog(seats, roles, roster)
    res = build_team([{"role": p} for p in roster.read_text().split()], load_catalog(roles), tmp_path / "out", comms="crew")
    return res["tools"][role], tool_cli_args(res["tools"][role])


@pytest.mark.parametrize("role", sorted(BUILD_ROLES))
def test_no_build_seat_can_push_use_the_network_or_run_arbitrary_bash(tmp_path, role):
    entry, args = _flags(tmp_path, role)
    assert "Bash" not in entry["allow"]                        # only specific patterns
    assert "Bash" not in entry["deny"]                          # (a bare deny would also cut off messaging)
    for rule in ("Bash(git push:*)", "Bash(git remote:*)", "Bash(curl:*)", "WebFetch", "WebSearch"):
        assert rule in entry["deny"], (role, rule)
    assert args[0] == "--allowedTools" and "--permission-mode" in args and "dontAsk" in args
    assert not any(a.startswith("Bash(git push") for a in entry["allow"])


def test_write_access_is_only_the_developers(tmp_path):
    for role in BUILD_ROLES:
        entry, _ = _flags(tmp_path / role, role)
        writes = {"Write", "Edit"} <= set(entry["allow"])
        assert writes == (role == "build-developer"), role
        if role != "build-developer":
            assert {"Write", "Edit"} <= set(entry["deny"]), role


def test_developer_can_commit_but_the_reviewer_can_only_read_history(tmp_path):
    dev, _ = _flags(tmp_path / "d", "build-developer")
    rev, _ = _flags(tmp_path / "r", "build-reviewer")
    assert "Bash(git commit:*)" in dev["allow"] and "Bash(git commit:*)" not in rev["allow"]
    assert "Bash(git diff:*)" in rev["allow"] and "Bash(npm test:*)" not in rev["allow"]


async def test_task_creation_rejects_an_unsafe_test_command(live_sessionmaker, tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@e", "-c", "user.name=t", "commit",
                    "-q", "--allow-empty", "-m", "i"], check=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        bad = await c.post("/tasks", json={"title": "t", "brief": "b", "output_type": "code",
                                           "workspace": {"repo_path": str(tmp_path), "test_command": "pytest; curl x"}})
        good = await c.post("/tasks", json={"title": "t", "brief": "b", "output_type": "code",
                                            "workspace": {"repo_path": str(tmp_path), "test_command": "pytest -q"}})
    assert bad.status_code == 422 and bad.json()["detail"]["error"]["code"] == "workspace_bad_test_command"
    assert good.status_code == 201
