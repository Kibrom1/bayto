"""Opening a pull request for a finished Build session, on the host, on the person's click.

The agents never push and hold no credentials (see `build_policy` and `git_guard`). When the person clicks
"Open pull request", the orchestrator -- running as them, on their machine -- pushes ONE branch (the session's
`bayto/build-<id8>`) to `origin` and runs `gh pr create`, using the git and `gh` logins that are already there.
Nothing is pushed to any other branch, nothing is merged, and the base branch is never touched.

Safety rules kept here:
- the branch name is derived from the session id, never taken from the request;
- git's own config and hooks are restored from the session's baseline before any git command runs, and hooks are
  switched off for the push anyway (`core.hooksPath=/dev/null`);
- the title and body are plain text passed as arguments / a file, never through a shell.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass

from .child_env import child_env

TIMEOUT_SECONDS = 120
TITLE_MAX = 120
BODY_MAX = 20_000
_URL = re.compile(r"https://[^\s]+/pull/\d+")


class PullRequestError(RuntimeError):
    """Something went wrong opening the pull request. `code` is stable; the message is shown to the person."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def gh_binary() -> str:
    return os.environ.get("BAYTO_GH", "gh")


def draft(change_set: dict, task_title: str | None = None) -> dict:
    """A title and body written from the change set's facts (commits, tests, review), not from a model."""
    commits = change_set.get("commits") or []
    title = (commits[-1]["subject"] if len(commits) == 1 else
             (task_title or (commits[-1]["subject"] if commits else change_set["branch"])))
    title = title.strip().splitlines()[0][:TITLE_MAX] if title and title.strip() else change_set["branch"]
    lines = []
    if change_set.get("summary"):
        lines += ["## Summary", "", change_set["summary"].strip(), ""]
    lines += ["## Changes", ""]
    lines += [f"- `{c['sha']}` {c['subject']}" for c in commits] or ["- No commits."]
    lines += ["", f"{change_set.get('files_changed', 0)} files changed, +{change_set.get('insertions', 0)} "
                  f"−{change_set.get('deletions', 0)}.", ""]
    tests = change_set.get("tests")
    lines += ["## Tests", ""]
    if tests:
        cmd = f"`{tests['command']}`" if tests.get("command") else "a test run"
        lines.append(f"The tester ran {cmd}; exit code {tests.get('exit_code', '?')}.")
    else:
        lines.append("No test run was reported.")
    review = change_set.get("review")
    lines += ["", "## Review", ""]
    lines.append(f"The reviewer's verdict: {review.get('verdict') or 'none'}." if review else "No review was reported.")
    if change_set.get("open_issues"):
        lines += ["", "## Open issues", ""] + [f"- {i}" for i in change_set["open_issues"]]
    lines += ["", "---", "Prepared by Bayto's agent team; opened by a person after reviewing the change set."]
    return {"title": title, "body": "\n".join(lines)[:BODY_MAX]}


def _run(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
                              stdin=subprocess.DEVNULL, env=child_env({"GIT_TERMINAL_PROMPT": "0"}))
    except FileNotFoundError:
        raise PullRequestError("tool_missing", f"'{args[0]}' is not installed on this machine.")
    except subprocess.TimeoutExpired:
        raise PullRequestError("timeout", f"'{args[0]}' did not finish in {TIMEOUT_SECONDS} seconds.")


def _tail(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stderr or proc.stdout or "").strip()[-400:]


@dataclass
class Opened:
    url: str
    head_sha: str
    remote_url: str


def remote_of(repo: str) -> str:
    out = _run(["git", "remote", "get-url", "origin"], repo)
    if out.returncode != 0 or not out.stdout.strip():
        raise PullRequestError("no_remote", "The repository has no 'origin' remote to push the branch to.")
    return out.stdout.strip()


def push_and_open(*, repo: str, branch: str, base: str, title: str, body: str, as_draft: bool) -> Opened:
    """Push `branch` to origin and open the pull request. Raises PullRequestError."""
    remote = remote_of(repo)
    sha = _run(["git", "rev-parse", "--verify", f"refs/heads/{branch}"], repo)
    if sha.returncode != 0:
        raise PullRequestError("no_branch", f"The branch {branch} no longer exists in the repository.")
    push = _run(["git", "-c", "core.hooksPath=/dev/null", "push", "--set-upstream", "origin",
                 f"refs/heads/{branch}:refs/heads/{branch}"], repo)
    if push.returncode != 0:
        raise PullRequestError("push_failed", f"git could not push {branch}: {_tail(push)}")
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
        fh.write(body)
        body_file = fh.name
    try:
        args = [gh_binary(), "pr", "create", "--head", branch, "--base", base, "--title", title, "--body-file", body_file]
        if as_draft:
            args.append("--draft")
        pr = _run(args, repo)
    finally:
        os.unlink(body_file)
    if pr.returncode != 0:
        raise PullRequestError("gh_failed", f"The branch was pushed, but gh could not open the pull request: {_tail(pr)}")
    found = _URL.search(pr.stdout or "")
    if not found:
        raise PullRequestError("gh_failed", "The branch was pushed, but gh did not report a pull request link.")
    return Opened(url=found.group(0), head_sha=sha.stdout.strip(), remote_url=re.sub(r"//[^/@]*@", "//", remote))
