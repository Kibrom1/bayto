"""Build mode B1: task.workspace and its preflight, against real throwaway git repos."""
import subprocess

import httpx
import pytest

from orchestrator.app import app
from orchestrator.models import Task
from orchestrator.workspace import preflight_workspace, valid_branch_name


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("a")
    _git(r, "add", "a.txt")
    _git(r, "commit", "-q", "-m", "init")
    return r


@pytest.mark.parametrize("name,ok", [
    ("main", True), ("release/1.2", True), ("feat_x-1", True),
    ("-rf", False), ("a..b", False), ("x.lock", False), ("a b", False), ("", False), ("a@{1}", False),
])
def test_branch_name_validation(name, ok):
    assert valid_branch_name(name) is ok


def test_clean_repo_passes(repo):
    rep = preflight_workspace(str(repo), "main")
    assert rep.ok and not rep.dirty and rep.current_branch == "main"


def test_dirty_tree_is_reported_but_not_blocking(repo):
    (repo / "a.txt").write_text("changed")
    (repo / "new.txt").write_text("x")
    rep = preflight_workspace(str(repo), "main")
    assert rep.ok and rep.dirty and sorted(rep.dirty_files) == ["a.txt", "new.txt"]


@pytest.mark.parametrize("make,code", [
    (lambda r, tp: "relative/path", "path_not_absolute"),
    (lambda r, tp: str(tp / "nope"), "path_not_found"),
    (lambda r, tp: str(tp), "not_a_git_repo"),
])
def test_bad_paths(repo, tmp_path, make, code):
    rep = preflight_workspace(make(repo, tmp_path), "main")
    assert not rep.ok and rep.problems[0]["code"] == code


def test_subfolder_is_rejected(repo):
    sub = repo / "src"
    sub.mkdir()
    assert preflight_workspace(str(sub), "main").problems[0]["code"] == "not_repo_root"


def test_missing_base_branch(repo):
    assert preflight_workspace(str(repo), "develop").problems[0]["code"] == "base_branch_missing"


# ---------------------------------------------------------------- API

async def _post(path, body):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        return await c.post(path, json=body)


async def test_preflight_endpoint(repo):
    resp = await _post("/workspaces/preflight", {"repo_path": str(repo)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] and body["current_branch"] == "main" and body["dirty"] is False


async def test_preflight_reports_a_bad_test_command_like_task_creation_does(repo):
    for cmd in ("pytest; rm -rf /", "pytest | tee x", "pytest $(id)"):
        body = (await _post("/workspaces/preflight", {"repo_path": str(repo), "test_command": cmd})).json()
        assert body["ok"] is False and body["problems"][0]["code"] == "bad_test_command", cmd
    good = (await _post("/workspaces/preflight", {"repo_path": str(repo), "test_command": "pytest -q && npm test"})).json()
    assert good["ok"] is True


async def test_task_with_workspace_is_stored(live_sessionmaker, repo):
    resp = await _post("/tasks", {"title": "t", "brief": "b", "output_type": "code",
                                  "workspace": {"repo_path": str(repo), "test_command": "pytest -q"}})
    assert resp.status_code == 201
    ws = resp.json()["task"]["workspace"]
    assert ws["base_branch"] == "main" and ws["test_command"] == "pytest -q"
    async with live_sessionmaker() as db:
        row = await db.get(Task, __import__("uuid").UUID(resp.json()["task"]["id"]))
        assert row.workspace["repo_path"] == ws["repo_path"]


async def test_task_with_bad_workspace_is_422(live_sessionmaker, tmp_path):
    resp = await _post("/tasks", {"title": "t", "brief": "b", "output_type": "code",
                                  "workspace": {"repo_path": str(tmp_path / "missing")}})
    assert resp.status_code == 422
    err = resp.json()["detail"]["error"]
    assert err["code"] == "workspace_path_not_found" and err["field"] == "workspace"


async def test_task_without_workspace_still_works(live_sessionmaker):
    resp = await _post("/tasks", {"title": "t", "brief": "b", "output_type": "text"})
    assert resp.status_code == 201 and resp.json()["task"]["workspace"] is None


async def test_put_workspace_on_an_existing_task(live_sessionmaker, repo):
    created = await _post("/tasks", {"title": "t", "brief": "b", "output_type": "decision"})
    task_id = created.json()["task"]["id"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        ok = await c.put(f"/tasks/{task_id}/workspace", json={"repo_path": str(repo), "test_command": "make test"})
        bad = await c.put(f"/tasks/{task_id}/workspace", json={"repo_path": str(repo), "test_command": "make; rm -rf /"})
        missing = await c.put("/tasks/11111111-1111-1111-1111-111111111111/workspace", json={"repo_path": str(repo)})
        listed = (await c.get("/tasks")).json()
    assert ok.status_code == 200 and ok.json()["task"]["workspace"]["test_command"] == "make test"
    assert bad.status_code == 422 and bad.json()["detail"]["error"]["code"] == "workspace_bad_test_command"
    assert missing.status_code == 404
    assert [t for t in listed if t["id"] == task_id][0]["workspace"]["base_branch"] == "main"


# ---- B2: the branch the sandbox will mount

from orchestrator.workspace import WorkspaceError, build_branch_name, prepare_branch  # noqa: E402


def _head(repo):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def test_prepare_branch_creates_it_from_the_base_and_checks_it_out(repo):
    assert prepare_branch(str(repo), "main", "bayto/build-abcd1234") is True
    assert _head(repo) == "bayto/build-abcd1234"


def test_prepare_branch_resumes_an_existing_branch(repo):
    prepare_branch(str(repo), "main", "bayto/build-abcd1234")
    _git(repo, "switch", "main")
    assert prepare_branch(str(repo), "main", "bayto/build-abcd1234") is False
    assert _head(repo) == "bayto/build-abcd1234"


def test_prepare_branch_refuses_a_dirty_tree_and_leaves_it_alone(repo):
    (repo / "a.txt").write_text("edited")
    with pytest.raises(WorkspaceError, match="uncommitted changes.*a.txt"):
        prepare_branch(str(repo), "main", "bayto/build-abcd1234")
    assert _head(repo) == "main" and (repo / "a.txt").read_text() == "edited"


def test_prepare_branch_refuses_a_missing_base_and_a_bad_name(repo):
    with pytest.raises(WorkspaceError, match="no local branch"):
        prepare_branch(str(repo), "nope", "bayto/build-abcd1234")
    with pytest.raises(WorkspaceError, match="usable branch name"):
        prepare_branch(str(repo), "main", "-x")


def test_session_branch_name_is_the_one_prepare_branch_makes(repo):
    sid = "0123abcd-1111-2222-3333-444455556666"
    prepare_branch(str(repo), "main", build_branch_name(sid))
    assert _head(repo) == "bayto/build-0123abcd"


async def test_allowed_commands_are_saved_with_the_task_and_checked_up_front(live_sessionmaker, repo):
    ok = await _post("/tasks", {"title": "t", "brief": "b", "output_type": "code", "workspace": {
        "repo_path": str(repo), "test_command": "make test", "allowed_commands": ["make lint", "uv sync"]}})
    assert ok.status_code == 201 and ok.json()["task"]["workspace"]["allowed_commands"] == ["make lint", "uv sync"]
    for bad in ("make lint | tee x", "git push origin main", "curl https://x.example"):
        resp = await _post("/tasks", {"title": "t", "brief": "b", "output_type": "code", "workspace": {
            "repo_path": str(repo), "allowed_commands": [bad]}})
        assert resp.status_code == 422 and resp.json()["detail"]["error"]["code"] == "workspace_bad_allowed_commands", bad
        pre = (await _post("/workspaces/preflight", {"repo_path": str(repo), "allowed_commands": [bad]})).json()
        assert pre["ok"] is False and pre["problems"][0]["code"] == "bad_allowed_commands", bad
