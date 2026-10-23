"""Builds a team sandbox for a session's roster by running the repo's proven pipeline, dev-team/run.sh.

`sbx env create` on its own gives an empty sandbox: the workshop launcher then delivers the support
tools (start-team, handoff, crew), the per-seat briefs, team.tsv and tool-permission flags into
$HOME/work and runs `prepare`. dev-team/run.sh does all of that. This module turns the session's
agents into the inputs it reads: a role catalog (one .yaml + .md per agent) and a roster file, so the
seats match the agents the moderator will address by `Agent.role`.

Seats are read-only by default (Read, Grep, Glob, plus the messaging commands the roster builder adds):
a discussion should not edit the repo mounted into the sandbox. An agent's own `tools` list widens that.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from pathlib import Path

import yaml

from ..child_env import child_env
from ..providers import provider_id_of
from ..providers.gateway import gateway_host
from .provider import SandboxProviderError, SeatSpec

REPO_ROOT = Path(__file__).resolve().parents[4]
READ_ONLY = ["Read", "Grep", "Glob"]
GATED = ["Write", "Edit", "WebFetch", "Bash"]  # denied unless the agent's tools list asks for them
_BUILD_LOCK = asyncio.Lock()  # dev-team/run.sh builds into one shared dev-team/.build directory


def seat_id(role: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", role.lower()).strip("-") or "seat"


def seat_permissions(seat: SeatSpec) -> tuple[list[str], list[str]]:
    """(allowed, denied) tools for a seat. The one place this is decided: used to build the role files and to
    record, in the audit trail, what each seat was allowed to do."""
    allow = READ_ONLY + [t for t in seat.tools if t not in READ_ONLY]
    deny = [t for t in GATED if t not in allow] + [t for t in seat.deny if t not in allow]
    return allow, deny


def _network_for(seat: SeatSpec) -> list[str]:
    """Hosts the seat's sandbox must reach. A seat on a gateway model adds the gateway's host, and only that seat does.
    (The sandbox's allow-list is the union over its seats, so the host is reachable by the whole team's sandbox; the
    gateway TOKEN still goes to the gateway seat's own process only.)"""
    hosts = ["api.anthropic.com"]
    if provider_id_of(seat.model) == "gateway" and (host := gateway_host()):
        hosts.append(host)
    return hosts


def write_catalog(seats: list[SeatSpec], roles_dir: Path, roster_file: Path) -> list[str]:
    """Write one role (.yaml + .md) per distinct seat plus a roster file. Returns the seat ids."""
    roles_dir.mkdir(parents=True, exist_ok=True)
    lines, ids = [], []
    for seat in seats:
        rid = seat_id(seat.role)
        if rid in ids:
            continue
        ids.append(rid)
        allow, deny = seat_permissions(seat)
        (roles_dir / f"{rid}.yaml").write_text(yaml.safe_dump({
            "id": rid, "name": seat.name, "responsibility": f"{seat.name} takes part in the discussion.",
            "brief": f"roles/{rid}.md",
            "may_send": ["report", "critique", "question", "answer"],
            "receives": ["assignment", "report", "question", "answer", "resume"],
            "tools": {"allow": allow, "deny": deny},
            "team_needs": {"network": _network_for(seat)},
        }, sort_keys=False))
        (roles_dir / f"{rid}.md").write_text(f"# {seat.name}\n\n{seat.brief.strip()}\n")
        lines.append(f"{rid} {seat.model}" if seat.model else rid)
    roster_file.write_text("\n".join(lines) + "\n")
    return ids


def _tail(text: str, n: int = 1500) -> str:
    return text[-n:].strip()


async def provision_team_sandbox(name: str, seats: list[SeatSpec], *, repo_root: Path = REPO_ROOT,
                                 timeout: float = 900.0, workspace: str | None = None) -> None:
    """Create sandbox `name` with one seat per entry of `seats` (runs dev-team/run.sh with SESSION=none).
    `workspace` is the host repository to mount read-write instead of this project's own (Build mode)."""
    script = repo_root / "dev-team" / "run.sh"
    if not script.exists():
        raise SandboxProviderError(f"{script} not found; cannot build the team sandbox")
    if not seats:
        raise SandboxProviderError("no seats to build a team sandbox for (empty roster)")
    work = Path(tempfile.mkdtemp(prefix="bayto-team-"))
    try:
        roles_dir, roster_file = work / "roles", work / "roster.txt"
        write_catalog(seats, roles_dir, roster_file)
        env = child_env({"ROSTER": str(roster_file), "BAYTO_ROLES_DIR": str(roles_dir),
                         "SESSION": "none", "NO_SWITCH": "1"})
        if workspace:
            env["BAYTO_WORKSPACE_REPO"] = workspace
        async with _BUILD_LOCK:
            proc = await asyncio.create_subprocess_exec(
                "bash", str(script), name, cwd=str(repo_root), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout)
            except asyncio.TimeoutError:
                proc.kill()
                out, _ = await proc.communicate()
                raise SandboxProviderError(
                    f"dev-team/run.sh did not finish within {timeout:.0f} s. Last output: {_tail(out.decode(errors='replace'))}")
        if proc.returncode != 0:
            raise SandboxProviderError(
                f"dev-team/run.sh exited {proc.returncode}: {_tail(out.decode(errors='replace'))}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
