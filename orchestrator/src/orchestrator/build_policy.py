"""Build mode (docs/build-mode-design.md, B5): what each Build seat may run.

Seats run `claude -p` with `--allowedTools` under `--permission-mode dontAsk`, so anything not
allowed is refused, and `--disallowedTools` wins over allow. This module only produces the two
lists; provisioning turns them into flags.

- The static per-role allowances live on the seeded agent templates (`seed.BUILD_TEMPLATES`).
- Added here, per session: the task's test command (for the developer and the tester) and a
  denylist applied to every Build seat as defence in depth, mainly `git push`, remotes and the network.
- The tester does not write files. Whether `claude -p` honours path-scoped rules such as
  `Write(tests/**)` is unverified, and a seat that can write anywhere is not a "tests only" seat.
  The developer writes the tests; the tester must fail the build when they are missing.
"""
from __future__ import annotations

import re

BUILD_ROLES = frozenset({"build-architect", "build-developer", "build-tester", "build-reviewer"})
RUNS_TESTS = frozenset({"build-developer", "build-tester"})

BUILD_DENY = (
    "Bash(git push:*)", "Bash(git remote:*)", "Bash(git config:*)", "Bash(git clone:*)", "Bash(git fetch:*)",
    "Bash(git pull:*)", "Bash(git reset --hard:*)", "Bash(git clean:*)",
    "Bash(curl:*)", "Bash(wget:*)", "Bash(ssh:*)", "Bash(scp:*)", "Bash(nc:*)",
    "Bash(rm -rf:*)", "Bash(sudo:*)",
    "WebFetch", "WebSearch",
    # Git runs hooks and config from .git on the host. These path rules are a first line only: a shell
    # redirect writes the same files, so git_guard.py also restores them after the session.
    "Write(.git/**)", "Edit(.git/**)", "Bash(git -c:*)",
)

# A test command may chain steps with `&&` ("cd web && npm test"). Anything that could smuggle in a
# second command or redirect output is refused up front so the allow rules below mean what they say.
_FORBIDDEN = re.compile(r"[;|`<>\n\r&]|\$\(")
_MAX_STEPS = 6


def parse_test_command(command: str) -> list[str]:
    """Split a test command into its `&&` steps, or raise ValueError saying why it is not allowed."""
    cmd = command.strip()
    if not cmd:
        raise ValueError("the test command is empty")
    if _FORBIDDEN.search(cmd.replace("&&", " ")):
        raise ValueError("use plain commands joined by && (no pipes, redirects, ; or $(...))")
    steps = [s.strip() for s in cmd.split("&&")]
    if any(not s for s in steps) or len(steps) > _MAX_STEPS:
        raise ValueError(f"expected 1 to {_MAX_STEPS} non-empty steps joined by &&")
    return steps


MAX_EXTRA_COMMANDS = 10


def _denied_heads() -> set[str]:
    """First words (or word pairs) of the Bash commands every Build seat is denied, e.g. "git push", "curl"."""
    heads = set()
    for rule in BUILD_DENY:
        if rule.startswith("Bash(") and rule.endswith(":*)"):
            heads.add(rule[len("Bash("):-len(":*)")])
    return heads


def validate_extra_commands(commands: list[str] | None) -> list[str]:
    """Extra commands the developer and tester may run, beyond the test command. Same syntax as the test command,
    and nothing the deny list already refuses. Returns the cleaned list or raises ValueError saying why not."""
    out: list[str] = []
    for raw in commands or []:
        cmd = raw.strip()
        if not cmd:
            continue
        steps = parse_test_command(cmd)  # raises for pipes, redirects, ;, $( ) and empty steps
        for step in steps:
            for head in _denied_heads():
                if step == head or step.startswith(head + " "):
                    raise ValueError(f"'{step}' can never be allowed: Build seats may not run '{head}'")
        out.append(cmd)
    if len(out) > MAX_EXTRA_COMMANDS:
        raise ValueError(f"at most {MAX_EXTRA_COMMANDS} extra commands")
    return out


def suggest_test_command(repo_path: str) -> str | None:
    """A guess at the repo's test command from the files it contains, for the form to pre-fill. Never trusted:
    the person confirms it, and parse_test_command still checks whatever they keep."""
    from pathlib import Path
    root = Path(repo_path)
    # Look in the root and one level down (a monorepo keeps the Python project in a subfolder).
    for base in [root, *sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))] \
            if root.is_dir() else []:
        prefix = "" if base == root else f"cd {base.name} && "
        if (base / "uv.lock").exists():
            return prefix + "uv run pytest"
        if (base / "pyproject.toml").exists() or (base / "pytest.ini").exists():
            return prefix + "python -m pytest"
        if (base / "package.json").exists():
            return prefix + "npm test"
        if (base / "Cargo.toml").exists():
            return prefix + "cargo test"
        if (base / "go.mod").exists():
            return prefix + "go test ./..."
    return None


def permission_summary(workspace: dict | None) -> list[dict]:
    """What each Build seat can run, for the setup page: [{role, can_run, cannot_run, note}]. Built from the same
    function the session uses (build_seat_tools), so the page cannot disagree with what the seat is given."""
    rows = []
    for role, base, note in (
        ("build-architect", (), "Plans and reads the code. No shell."),
        ("build-developer", ("Bash(git add:*)", "Bash(git commit:*)", "Bash(git status:*)", "Bash(git diff:*)",
                             "Bash(git log:*)"), "Edits files and commits to the build branch."),
        ("build-tester", ("Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)"),
         "Runs the test command and reports the exit code. Does not edit files."),
        ("build-reviewer", ("Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)"),
         "Reads the diff. No shell beyond git read commands."),
    ):
        allow, _deny = build_seat_tools(role, base, workspace)
        shell = [r[len("Bash("):-len(":*)")] for r in allow if r.startswith("Bash(")]
        rows.append({"role": role, "can_run": shell, "note": note,
                     "can_run_tests": any(not r.startswith("git ") for r in shell)})
    return rows


def command_rules(command: str | None, extra: list[str] | None = None) -> list[str]:
    rules: list[str] = []
    for cmd in [command, *(extra or [])]:
        if cmd:
            for step in parse_test_command(cmd):
                rule = f"Bash({step}:*)"
                if rule not in rules:
                    rules.append(rule)
    return rules


def build_seat_tools(role: str, base_tools: tuple[str, ...] | list[str],
                     workspace: dict | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(allowed tools, denied tools) for a seat. Non-Build roles are returned unchanged."""
    if role not in BUILD_ROLES:
        return tuple(base_tools), ()
    allow = list(base_tools)
    if role in RUNS_TESTS:
        # The tester runs the test command and nothing else; the developer also gets the extra commands
        # (a linter, a setup step). A seat that only checks should not be able to change the environment.
        extra = (workspace or {}).get("allowed_commands") if role == "build-developer" else None
        for rule in command_rules((workspace or {}).get("test_command"), extra):
            if rule not in allow:
                allow.append(rule)
    return tuple(allow), BUILD_DENY
