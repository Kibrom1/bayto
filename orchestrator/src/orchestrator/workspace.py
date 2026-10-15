"""Build mode (docs/build-mode-design.md, B1): a task's code workspace and its preflight.

A workspace is a local git repository the Build team may change on a throwaway branch. The
orchestrator runs on the developer's machine, so the path is a host path and the checks are plain
`git` calls. Nothing here writes to the repository.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# A branch name is passed to git as an argument, so keep it to a conservative character set and
# never let it start with "-" (option injection) or contain ".." / "@{" (revision syntax).
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
GIT_TIMEOUT = 15.0


def valid_branch_name(name: str) -> bool:
    return bool(_BRANCH_RE.match(name)) and ".." not in name and not name.endswith((".lock", "/", "."))


@dataclass
class WorkspaceReport:
    ok: bool = False                       # no blocking problems
    problems: list[dict] = field(default_factory=list)   # [{code, detail}], each one blocks a task from using the repo
    repo_root: str | None = None
    current_branch: str | None = None
    dirty: bool = False                    # uncommitted changes; blocks a Build session from starting, not task creation
    dirty_files: list[str] = field(default_factory=list)


def _git(repo: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=GIT_TIMEOUT)


def preflight_workspace(repo_path: str, base_branch: str) -> WorkspaceReport:
    rep = WorkspaceReport()

    def problem(code: str, detail: str) -> WorkspaceReport:
        rep.problems.append({"code": code, "detail": detail})
        return rep

    if not valid_branch_name(base_branch):
        return problem("bad_branch_name", f"'{base_branch}' is not a usable branch name.")
    p = Path(repo_path)
    if not p.is_absolute():
        return problem("path_not_absolute", "Use the full path to the repository, starting with /.")
    if not p.is_dir():
        return problem("path_not_found", f"{repo_path} does not exist or is not a folder.")
    try:
        top = _git(repo_path, "rev-parse", "--show-toplevel")
        if top.returncode != 0:
            return problem("not_a_git_repo", f"{repo_path} is not inside a git repository.")
        rep.repo_root = top.stdout.strip()
        if Path(rep.repo_root).resolve() != p.resolve():
            return problem("not_repo_root", f"Point at the repository root ({rep.repo_root}), not a sub-folder.")
        if _git(repo_path, "rev-parse", "--verify", "--quiet", f"refs/heads/{base_branch}").returncode != 0:
            return problem("base_branch_missing", f"The repository has no local branch '{base_branch}'.")
        head = _git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
        rep.current_branch = head.stdout.strip() or None
        status = _git(repo_path, "status", "--porcelain")
        if status.returncode == 0:
            rep.dirty_files = [ln[3:] for ln in status.stdout.splitlines()][:50]
            rep.dirty = bool(rep.dirty_files)
    except FileNotFoundError:
        return problem("git_missing", "git is not installed on this machine.")
    except subprocess.TimeoutExpired:
        return problem("git_timeout", "git took too long to answer; is the repository on a slow or offline drive?")
    rep.ok = not rep.problems
    return rep
