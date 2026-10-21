"""Pull request on the person's click: pushes only the session's branch to origin, opens the PR with gh, records it."""
import datetime as dt
import stat
import subprocess
import uuid

import httpx
import pytest

from orchestrator.app import app
from audit_helpers import audit_events
from orchestrator.models import Artifact, Mode, Session, Task
from orchestrator.moderator.change_set import build_change_set
from orchestrator.workspace import build_branch_name


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A repo with a local bare 'origin', a build branch with one commit, and a fake gh that logs its arguments."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = tmp_path / "p"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@e.com"); git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("x = 1\n")
    git(repo, "add", "."); git(repo, "commit", "-q", "-m", "init")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "main")
    sid = uuid.uuid4()
    branch = build_branch_name(sid)
    git(repo, "switch", "-q", "-c", branch)
    (repo / "b.py").write_text("y = 2\n")
    git(repo, "add", "."); git(repo, "commit", "-q", "-m", "add b")
    git(repo, "switch", "-q", "main")
    gh = tmp_path / "gh"
    log = tmp_path / "gh.log"
    gh.write_text(f'#!/bin/sh\necho "$@" >> {log}\ncat "${{10}}" >> {log}.body 2>/dev/null\n'
                  'if [ "$BAYTO_FAKE_GH_FAIL" = 1 ]; then echo "not logged in" >&2; exit 1; fi\n'
                  'echo "https://github.com/acme/app/pull/7"\n')
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("BAYTO_GH", str(gh))
    monkeypatch.setenv("BAYTO_FACTORY_ROOT", str(tmp_path / "factory"))
    return {"repo": repo, "origin": origin, "sid": sid, "branch": branch, "log": log}


async def _session(sm, w, *, ended=True, stop_reason="build_complete"):
    cs = build_change_set(workspace={"repo_path": str(w["repo"]), "base_branch": "main"}, session_id=w["sid"],
                          stop_reason=stop_reason, turns=[("build-tester", "ran `pytest -q`\nexit code 0"),
                                                          ("build-reviewer", "Looks good.\nVERDICT: approve")])
    async with sm() as db:
        task = Task(title="Add b module", output_type="code",
                    workspace={"repo_path": str(w["repo"]), "base_branch": "main"})
        mode = Mode(name=f"build-{uuid.uuid4().hex[:6]}")
        db.add_all([task, mode]); await db.flush()
        sess = Session(id=w["sid"], task_id=task.id, mode_id=mode.id, status="finished",
                       ended_at=dt.datetime.now(dt.timezone.utc) if ended else None)
        db.add(sess); await db.flush()
        db.add(Artifact(session_id=sess.id, type="change_set", content_json=cs))
        await db.commit()


async def test_get_returns_a_draft_written_from_the_facts(live_sessionmaker, world):
    await _session(live_sessionmaker, world)
    async with _client() as c:
        r = await c.get(f"/sessions/{world['sid']}/pull-request")
    body = r.json()
    assert r.status_code == 200 and body["ready"] is True and body["opened"] is None
    assert body["draft"]["title"] == "add b"
    assert "pytest -q" in body["draft"]["body"] and "approve" in body["draft"]["body"]


async def test_open_pushes_only_the_build_branch_and_records_it(live_sessionmaker, world, span_exporter):
    await _session(live_sessionmaker, world)
    async with _client() as c:
        r = await c.post(f"/sessions/{world['sid']}/pull-request", json={"title": "Add b", "body": "Body text"})
        again = await c.post(f"/sessions/{world['sid']}/pull-request", json={"title": "Add b", "body": "x"})
        got = await c.get(f"/sessions/{world['sid']}/pull-request")
    assert r.status_code == 201 and r.json()["url"] == "https://github.com/acme/app/pull/7"
    assert again.status_code == 409 and again.json()["detail"]["error"]["code"] == "already_opened"
    assert got.json()["opened"]["url"].endswith("/pull/7")
    # Only the build branch (and the already-present main) exist on the remote; main did not move.
    heads = git(world["origin"], "for-each-ref", "--format=%(refname:short)").split()
    assert sorted(heads) == sorted(["main", world["branch"]])
    assert git(world["origin"], "rev-parse", "main") == git(world["repo"], "rev-parse", "main")
    args = world["log"].read_text()
    assert f"--head {world['branch']}" in args and "--base main" in args and "--draft" not in args
    assert (world["log"].parent / "gh.log.body").read_text() == "Body text"
    [ev] = audit_events(span_exporter, action="pr.opened")
    assert ev.detail["url"].endswith("/pull/7") and ev.detail["branch"] == world["branch"] and ev.actor == "person"


async def test_a_session_that_did_not_finish_cleanly_needs_an_explicit_draft(live_sessionmaker, world):
    await _session(live_sessionmaker, world, stop_reason="user_stop")
    async with _client() as c:
        no = await c.post(f"/sessions/{world['sid']}/pull-request", json={"title": "t", "body": "b"})
        still = await c.post(f"/sessions/{world['sid']}/pull-request", json={"title": "t", "body": "b", "draft": True})
        yes = await c.post(f"/sessions/{world['sid']}/pull-request",
                           json={"title": "t", "body": "b", "draft": True, "open_unfinished": True})
    assert no.status_code == 409 and no.json()["detail"]["error"]["code"] == "not_ready"
    assert still.status_code == 409
    assert yes.status_code == 201 and yes.json()["draft"] is True
    assert "--draft" in world["log"].read_text()


async def test_running_session_and_missing_remote_are_refused(live_sessionmaker, world):
    await _session(live_sessionmaker, world, ended=False)
    async with _client() as c:
        r = await c.post(f"/sessions/{world['sid']}/pull-request", json={"title": "t", "body": "b"})
    assert r.json()["detail"]["error"]["code"] == "still_running"
    async with live_sessionmaker() as db:
        s = await db.get(Session, world["sid"]); s.ended_at = dt.datetime.now(dt.timezone.utc); await db.commit()
    git(world["repo"], "remote", "remove", "origin")
    async with _client() as c:
        r = await c.post(f"/sessions/{world['sid']}/pull-request", json={"title": "t", "body": "b"})
    assert r.status_code == 409 and r.json()["detail"]["error"]["code"] == "no_remote"


async def test_gh_failure_is_reported_and_audited_and_can_be_retried(live_sessionmaker, world, monkeypatch, span_exporter):
    await _session(live_sessionmaker, world)
    monkeypatch.setenv("BAYTO_FAKE_GH_FAIL", "1")
    async with _client() as c:
        r = await c.post(f"/sessions/{world['sid']}/pull-request", json={"title": "t", "body": "b"})
        monkeypatch.delenv("BAYTO_FAKE_GH_FAIL")
        ok = await c.post(f"/sessions/{world['sid']}/pull-request", json={"title": "t", "body": "b"})
    assert r.status_code == 502 and r.json()["detail"]["error"]["code"] == "gh_failed" and "not logged in" in r.json()["detail"]["error"]["detail"]
    assert ok.status_code == 201
    actions = [e.action for e in audit_events(span_exporter)]
    assert actions == ["pr.failed", "pr.opened"]


async def test_hooks_do_not_run_during_the_push(live_sessionmaker, world, tmp_path):
    marker = tmp_path / "hook-ran"
    hook = world["repo"] / ".git" / "hooks" / "pre-push"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n"); hook.chmod(0o755)
    await _session(live_sessionmaker, world)
    async with _client() as c:
        r = await c.post(f"/sessions/{world['sid']}/pull-request", json={"title": "t", "body": "b"})
    assert r.status_code == 201 and not marker.exists()


async def test_unknown_session_and_missing_change_set(live_sessionmaker):
    async with _client() as c:
        assert (await c.get(f"/sessions/{uuid.uuid4()}/pull-request")).status_code == 404
