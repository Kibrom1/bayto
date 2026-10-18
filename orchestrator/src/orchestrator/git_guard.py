"""Build mode: stop a session's agents from planting code in the repository's own `.git`.

The sandbox mounts the user's repository read-write, `.git` included. Git runs hooks, the
`core.fsmonitor` / `core.sshCommand` / `diff.external` settings and similar from `.git/config` and
`.git/hooks` on the HOST, the next time anyone runs a git command there. A seat that can write files can
therefore make its output run on the developer's machine. Path denies on the file tools are not enough
(a shell redirect writes the same file, and whether `claude -p` honours path rules is unverified), so this
module works from the other end:

1. before the sandbox starts, `save_baseline` records the guarded files (once per session; a resume keeps
   the first baseline, so tampering cannot be "blessed" by a restart);
2. before the orchestrator runs any git command on the host to describe the result, `check_and_restore`
   compares them with the baseline, puts the baseline back and returns what it had to undo.

Guarded: `.git/config`, `.git/config.worktree`, `.git/info/attributes` and every non-`.sample` file in
`.git/hooks`. Anything else under `.git` is left alone.
"""
from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path

GUARDED_FILES = ("config", "config.worktree", "info/attributes")
HOOKS_DIR = "hooks"
_MAX_BYTES = 1_000_000  # a hook or config bigger than this is not read; it is treated as changed


def _git_dir(repo_path: str) -> Path | None:
    d = Path(repo_path) / ".git"
    return d if d.is_dir() and not d.is_symlink() else None  # a `.git` file (worktree, submodule) is not guarded


def _read(path: Path) -> dict | None:
    """{kind, data, mode} for a regular file; {kind: other} for a symlink or directory; None if absent."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return {"kind": "other"}
    if st.st_size > _MAX_BYTES:
        return {"kind": "big", "size": st.st_size}
    return {"kind": "file", "data": base64.b64encode(path.read_bytes()).decode(), "mode": stat.S_IMODE(st.st_mode)}


def snapshot(repo_path: str) -> dict | None:
    gd = _git_dir(repo_path)
    if gd is None:
        return None
    files = {rel: _read(gd / rel) for rel in GUARDED_FILES}
    hooks = {}
    hdir = gd / HOOKS_DIR
    if hdir.is_dir() and not hdir.is_symlink():
        for entry in sorted(os.listdir(hdir)):
            if not entry.endswith(".sample"):
                hooks[entry] = _read(hdir / entry)
    return {"files": files, "hooks": hooks}


def save_baseline(repo_path: str, baseline_file: Path) -> bool:
    """Write the baseline unless one already exists. True when a new baseline was written."""
    if baseline_file.exists():
        return False
    snap = snapshot(repo_path)
    if snap is None:
        return False
    baseline_file.parent.mkdir(parents=True, exist_ok=True)
    baseline_file.write_text(json.dumps(snap))
    return True


def _restore(path: Path, entry: dict | None) -> None:
    """Put `path` back to `entry` (None = it did not exist)."""
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            return  # never recurse into something we did not write
    except FileNotFoundError:
        pass
    if entry and entry.get("kind") == "file":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(entry["data"]))
        os.chmod(path, entry["mode"])


def check_and_restore(repo_path: str, baseline_file: Path) -> list[str]:
    """Undo any change to the guarded files and return one plain sentence per change ([] = untouched).
    No baseline, or a repository without a `.git` directory, means nothing to compare: returns []."""
    gd = _git_dir(repo_path)
    if gd is None or not baseline_file.exists():
        return []
    try:
        base = json.loads(baseline_file.read_text())
    except (OSError, ValueError):
        return []
    now = snapshot(repo_path) or {"files": {}, "hooks": {}}
    changes: list[str] = []
    for rel in GUARDED_FILES:
        if now["files"].get(rel) != base["files"].get(rel):
            changes.append(f".git/{rel} was changed")
            _restore(gd / rel, base["files"].get(rel))
    for name in sorted(set(base["hooks"]) | set(now["hooks"])):
        before, after = base["hooks"].get(name), now["hooks"].get(name)
        if before == after:
            continue
        changes.append(f"git hook '{name}' was " + ("added" if before is None else "removed" if after is None else "changed"))
        _restore(gd / HOOKS_DIR / name, before)
    return changes
