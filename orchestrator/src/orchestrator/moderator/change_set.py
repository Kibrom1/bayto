"""Build mode (B7): the `change_set` artifact -- what a Build session left on its branch.

Facts come from git (commits, files, line counts) and from the transcript's own reports (the tester's
command and exit code, the reviewer's notes). Nothing here asks a model to say whether tests passed: the
`status` is `ready_to_merge` only when the pipeline ended in `build-complete`, which the policy only
allows after a tester report with a command and an exit code of 0 and a reviewer approval.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..floor.pipeline import _COMMAND_RE, _EXIT_RE, parse_verdict
from ..git_guard import check_and_restore
from ..workspace import build_branch_name, collect_branch_changes

TAIL_LINES = 15
_VERDICT_LINE = re.compile(r"^\s*\**VERDICT\**\s*:.*$", re.IGNORECASE | re.MULTILINE)
_BACKTICK = re.compile(r"`([^`\n]+)`")


def _body(text: str) -> str:
    return _VERDICT_LINE.sub("", text or "").strip()


def evidence_from_report(text: str) -> dict | None:
    """The tester's own words, reduced to {command, exit_code, tail}; None if it showed nothing runnable."""
    body = _body(text)
    codes = _EXIT_RE.findall(body)
    cmd = _BACKTICK.search(body)
    if not codes and not cmd:
        return None
    if cmd is None and (m := re.search(r"^\s*\$ (.+)$", body, re.MULTILINE)):
        command = m.group(1).strip()
    else:
        command = cmd.group(1).strip() if cmd else None
    return {"command": command, "exit_code": int(codes[-1]) if codes else None,
            "tail": "\n".join(body.splitlines()[-TAIL_LINES:])}


def review_from_report(text: str) -> dict:
    return {"verdict": parse_verdict(text), "notes": _body(text)}


def build_change_set(*, workspace: dict, session_id, stop_reason: str, turns: list[tuple[str, str]],
                     summary: str | None = None, guard_file: Path | None = None) -> dict | None:
    """`turns` is [(role, content)] in order. Returns the artifact's content_json, or None when there is
    no branch to describe (the pipeline never got as far as creating one)."""
    branch = build_branch_name(session_id)
    # First, before the host runs any git command in the repo: undo changes to git's own config and hooks.
    guard_changes = check_and_restore(workspace["repo_path"], guard_file) if guard_file else []
    changes = collect_branch_changes(workspace["repo_path"], workspace.get("base_branch", "main"), branch)
    if changes is None:
        return None
    def last_by(suffix: str) -> str | None:  # "build-tester", "tester" ... whatever the seat is called
        found = [content for role, content in turns if role.endswith(suffix)]
        return found[-1] if found else None

    tester, reviewer = last_by("tester"), last_by("reviewer")
    developer_turns = sum(1 for role, _ in turns if role.endswith("developer"))
    complete = stop_reason == "build_complete"
    open_issues = []
    if not complete:
        open_issues.append(f"The session ended early ({stop_reason}); review the branch before trusting it.")
    if not changes["commits"]:
        open_issues.append("No commits landed on the branch.")
    if guard_changes:
        open_issues.append(
            "The agents changed git's own settings inside the repository (" + "; ".join(guard_changes) + "). "
            "Bayto put them back, but look at the commits and the diff with extra care before you merge.")
    return {
        "branch": branch,
        "base_branch": workspace.get("base_branch", "main"),
        "switch_command": f"git switch {branch}",
        **changes,
        "tests": evidence_from_report(tester) if tester is not None else None,
        "test_command": workspace.get("test_command"),
        "review": review_from_report(reviewer) if reviewer is not None else None,
        "fix_cycles": max(developer_turns - 1, 0),
        "git_guard": {"changed": guard_changes, "restored": bool(guard_changes)},
        "status": "ready_to_merge" if complete and changes["commits"] and not guard_changes else "needs_you",
        "open_issues": open_issues,
        "summary": summary,
    }
