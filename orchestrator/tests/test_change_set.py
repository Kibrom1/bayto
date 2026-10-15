"""Build mode B7: the change_set artifact is read from git and the transcript, never invented."""
import subprocess
import uuid

import pytest

from orchestrator.moderator.change_set import build_change_set, review_from_report, evidence_from_report
from orchestrator.workspace import build_branch_name, collect_branch_changes


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "p"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@e.com")
    git(r, "config", "user.name", "t")
    (r / "a.py").write_text("x = 1\n")
    git(r, "add", "."); git(r, "commit", "-q", "-m", "init")
    return r


def commit_on(repo, branch, name, text, msg):
    git(repo, "switch", "-q", "-C", branch) if branch not in _branches(repo) else git(repo, "switch", "-q", branch)
    (repo / name).write_text(text)
    git(repo, "add", name); git(repo, "commit", "-q", "-m", msg)
    git(repo, "switch", "-q", "main")


def _branches(repo):
    out = subprocess.run(["git", "-C", str(repo), "branch", "--format=%(refname:short)"], capture_output=True, text=True)
    return out.stdout.split()


def test_branch_name_is_derived_from_the_session_id():
    sid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    assert build_branch_name(sid) == "bayto/build-12345678"


def test_missing_branch_is_none(repo):
    assert collect_branch_changes(str(repo), "main", "bayto/none") is None


def test_collects_commits_files_and_line_counts(repo):
    commit_on(repo, "bayto/b", "b.py", "a\nb\nc\n", "add b")
    commit_on(repo, "bayto/b", "a.py", "x = 2\n", "change a")
    ch = collect_branch_changes(str(repo), "main", "bayto/b")
    assert [c["subject"] for c in ch["commits"]] == ["change a", "add b"]  # newest first
    assert ch["files_changed"] == 2 and ch["insertions"] == 4 and ch["deletions"] == 1
    assert {(f["status"], f["path"]) for f in ch["files"]} == {("A", "b.py"), ("M", "a.py")}


def test_a_branch_with_no_commits_reports_none_landed(repo):
    git(repo, "branch", "bayto/empty")
    ch = collect_branch_changes(str(repo), "main", "bayto/empty")
    assert ch["commits"] == [] and ch["files_changed"] == 0


def test_evidence_from_report_extracts_command_exit_code_and_tail():
    t = evidence_from_report("Ran `pytest -q`\nexit code: 0\n12 passed\nVERDICT: pass")
    assert t["command"] == "pytest -q" and t["exit_code"] == 0 and "12 passed" in t["tail"] and "VERDICT" not in t["tail"]
    assert evidence_from_report("All good.") is None
    assert evidence_from_report("```\n$ npm test\n```\nexited with 1")["exit_code"] == 1


def test_review_from_report():
    r = review_from_report("1. rename x\nVERDICT: changes")
    assert r == {"verdict": "changes", "notes": "1. rename x"}


def _ws(repo):
    return {"repo_path": str(repo), "base_branch": "main", "test_command": "pytest -q"}


TURNS = [("build-architect", "plan\nVERDICT: planned"), ("build-developer", "done\nVERDICT: implemented"),
         ("build-tester", "`pytest -q`\nexit code: 1\nVERDICT: fail"), ("build-developer", "fixed\nVERDICT: implemented"),
         ("build-tester", "`pytest -q`\nexit code: 0\n3 passed\nVERDICT: pass"),
         ("build-reviewer", "Reads well.\nVERDICT: approve")]


def test_complete_build_is_ready_to_merge_and_counts_fix_cycles(repo):
    sid = uuid.uuid4()
    commit_on(repo, build_branch_name(sid), "b.py", "1\n", "feat")
    cs = build_change_set(workspace=_ws(repo), session_id=sid, stop_reason="build_complete", turns=TURNS, summary="s")
    assert cs["status"] == "ready_to_merge" and cs["fix_cycles"] == 1 and cs["open_issues"] == []
    assert cs["tests"]["exit_code"] == 0 and cs["review"]["verdict"] == "approve"
    assert cs["switch_command"] == f"git switch {build_branch_name(sid)}" and cs["summary"] == "s"


def test_an_early_stop_needs_you_even_with_commits(repo):
    sid = uuid.uuid4()
    commit_on(repo, build_branch_name(sid), "b.py", "1\n", "feat")
    cs = build_change_set(workspace=_ws(repo), session_id=sid, stop_reason="user_stop", turns=TURNS[:3])
    assert cs["status"] == "needs_you" and "ended early" in cs["open_issues"][0]
    assert cs["tests"]["exit_code"] == 1 and cs["review"] is None


def test_no_branch_means_no_change_set(repo):
    assert build_change_set(workspace=_ws(repo), session_id=uuid.uuid4(), stop_reason="build_complete", turns=TURNS) is None


def test_complete_without_commits_is_not_ready(repo):
    sid = uuid.uuid4()
    git(repo, "branch", build_branch_name(sid))
    cs = build_change_set(workspace=_ws(repo), session_id=sid, stop_reason="build_complete", turns=TURNS)
    assert cs["status"] == "needs_you" and "No commits landed on the branch." in cs["open_issues"]
