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
