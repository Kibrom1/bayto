"""LocalSbxSandboxProvider: drives the local `sbx` CLI (M2.4).

Injectable subprocess seam (`SbxRunner`), same shape as mirror.py wrapping its sync
transport in `asyncio.to_thread`. Each lifecycle method opens one
`async with self._sessionmaker() as session:` block to read/write the single Sandbox row
for that task (insert on create, update status/closed_at otherwise), same
open-session-per-call shape as `MessageMirror.poll_once`.

CLI mapping, per docs/product-design.md's local-sbx table. Only `wake_role` and the
`env create` shape of `create` are quoted verbatim there; the rest are this provider's
best-fit extrapolation of the same command family and are UNVERIFIED against a real `sbx`
binary (none is available in this dev-team sandbox -- same caveat class as the M1.10-M1.15
skip decision):
  - stop/rm subcommand spelling beyond "rm"
  - whether `sbx ls` has a `--json` mode and what its output schema is (assumed here: a
    JSON list of objects with "name" and "status")
  - whether `start-team` needs an explicit `exec` call or fires automatically as a
    post-create hook from team.sbxenv.yaml
  - the exact stdout of `env create` (how it surfaces the sandbox id/name to persist)
Verify all of the above from a host with the real `sbx` CLI before relying on this in
production; until then, treat `SubprocessSbxRunner` as unverified glue and `LocalSbxSandboxProvider`
as verified only at the DB/argv-shape level (see tests/test_local_sandbox.py).
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models import Sandbox
from .provider import (
    SandboxCommandError,
    SandboxDrift,
    SandboxInfo,
    SandboxProvider,
    SandboxProviderError,
)

PROVIDER_NAME = "local-sbx"

# Clear a stale marker (a leftover one makes start-team exit at once and start nothing), start the
# team, then keep this exec alive: the team dies when the exec that started it returns.
TEAM_SCRIPT = "rm -f $HOME/work/factory/team-started; start-team; sleep infinity"
TEAM_READY_PROBE = 'test -f "$HOME/work/factory/team-started"'


class HeldExec(Protocol):
    """A long-lived `sbx exec` the provider keeps open. Processes started inside a sandbox by
    `sbx exec` die when that exec returns (M1.12), so the team lives exactly as long as this does."""

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def log_tail(self) -> str: ...


class SbxRunner(Protocol):
    def run(self, argv: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]: ...
    def start(self, argv: list[str]) -> HeldExec: ...


class _PopenHeldExec:
    def __init__(self, proc: subprocess.Popen, log_path: str) -> None:
        self._proc = proc
        self._log_path = log_path

    def poll(self) -> int | None:
        return self._proc.poll()

    def terminate(self) -> None:
        self._proc.terminate()

    def log_tail(self) -> str:
        try:
            with open(self._log_path, errors="replace") as f:
                return f.read()[-2000:]
        except OSError:
            return ""


class SubprocessSbxRunner:
    """Real `sbx` CLI invocation. Unverified: see module docstring."""

    def run(self, argv: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["sbx", *argv], capture_output=True, text=True, timeout=timeout, check=False)

    def start(self, argv: list[str]) -> HeldExec:
        log = tempfile.NamedTemporaryFile(prefix="sbx-held-", suffix=".log", delete=False)
        proc = subprocess.Popen(["sbx", *argv], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        log.close()
        return _PopenHeldExec(proc, log.name)


@dataclass(frozen=True, slots=True)
class SandboxRow:
    """Plain snapshot of a DB Sandbox row, for the zero-I/O `_diff`."""

    task_id: uuid.UUID
    name: str
    status: str


def _sandbox_info(row: Sandbox) -> SandboxInfo:
    return SandboxInfo(
        id=row.id,
        task_id=row.task_id,
        provider=row.provider,
        name=row.name,
        status=row.status,
        image=row.image,
        created_at=row.created_at,
        closed_at=row.closed_at,
    )


def _parse_sbx_ls(stdout: str) -> dict[str, str]:
    """Parses `sbx ls --json` output into {name: status}. UNVERIFIED shape (see module
    docstring) -- assumed to be a JSON list of objects each with "name" and "status"."""
    if not stdout.strip():
        return {}
    entries = json.loads(stdout)
    return {entry["name"]: entry["status"] for entry in entries}


def _diff(live: dict[str, str], rows: list[SandboxRow]) -> list[SandboxDrift]:
    """Pure function: no I/O. `reconcile()` is just call sbx ls -> parse -> load DB rows ->
    _diff -> apply the "synced"/"marked_removed" updates -> return."""
    drifts: list[SandboxDrift] = []
    row_by_name = {row.name: row for row in rows}

    for row in rows:
        if row.name in live:
            drifts.append(SandboxDrift(
                task_id=row.task_id, name=row.name,
                db_status=row.status, live_status=live[row.name], resolution="synced",
            ))
        else:
            drifts.append(SandboxDrift(
                task_id=row.task_id, name=row.name,
                db_status=row.status, live_status=None, resolution="marked_removed",
            ))

    for name, status in live.items():
        if name not in row_by_name:
            drifts.append(SandboxDrift(
                task_id=None, name=name,
                db_status=None, live_status=status, resolution="orphan_reported",
            ))

    return drifts


class LocalSbxSandboxProvider(SandboxProvider):
    def __init__(self, sessionmaker: async_sessionmaker, *, runner: SbxRunner | None = None,
                 sbxenv_path: Path = Path("team.sbxenv.yaml"),
                 team_ready_timeout: float = 600.0, team_poll_interval: float = 5.0) -> None:
        self._sessionmaker = sessionmaker
        self._runner = runner or SubprocessSbxRunner()
        self._sbxenv_path = sbxenv_path
        # 8 agents took 437 s to start in M1.12; 600 s leaves headroom.
        self._team_ready_timeout = team_ready_timeout
        self._team_poll_interval = team_poll_interval
        self._held: dict[str, HeldExec] = {}

    def _release(self, name: str) -> None:
        held = self._held.pop(name, None)
        if held is not None and held.poll() is None:
            held.terminate()

    async def close(self) -> None:
        """Release every held exec (orchestrator shutdown). The teams die with them."""
        for name in list(self._held):
            self._release(name)

    async def _run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        result = await asyncio.to_thread(self._runner.run, argv)
        if result.returncode != 0:
            raise SandboxCommandError(argv, result.returncode, result.stderr)
        return result

    async def create(self, task_id: uuid.UUID, *, name: str) -> SandboxInfo:
        async with self._sessionmaker() as session:
            row = await session.scalar(select(Sandbox).where(Sandbox.task_id == task_id))
            if row is None or row.status not in ("running", "creating"):
                await self._run([
                    "env", "create", str(self._sbxenv_path), "--env-arg", f"name={name}", "--auto-approve",
                ])
                if row is None:
                    row = Sandbox(
                        task_id=task_id, provider=PROVIDER_NAME, name=name,
                        status="running", created_at=datetime.now(timezone.utc),
                    )
                    session.add(row)
                else:
                    row.name = name
                    row.status = "running"
                    row.closed_at = None
            await session.commit()
            await session.refresh(row)
            return _sandbox_info(row)

    async def start_team(self, sandbox: SandboxInfo) -> None:
        """Start the team under a held-open exec and wait until `team-started` appears.

        No-op if a live holder already exists. Raises SandboxProviderError if the holder exits
        early or the team is not up within the timeout; the holder is released in both cases."""
        name = sandbox.name
        held = self._held.get(name)
        if held is not None and held.poll() is None:
            return
        self._release(name)
        held = self._runner.start(["exec", name, "bash", "-lc", TEAM_SCRIPT])
        self._held[name] = held
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._team_ready_timeout
        while True:
            code = held.poll()
            if code is not None:
                tail = held.log_tail()
                self._release(name)
                raise SandboxProviderError(f"start-team exec for {name} exited with {code} before the team was up: {tail}")
            probe = await asyncio.to_thread(self._runner.run, ["exec", name, "bash", "-lc", TEAM_READY_PROBE])
            if probe.returncode == 0:
                return
            if loop.time() >= deadline:
                tail = held.log_tail()
                self._release(name)
                raise SandboxProviderError(
                    f"team-started not seen in {name} after {self._team_ready_timeout:.0f} s: {tail}")
            await asyncio.sleep(self._team_poll_interval)

    async def restart_team(self, sandbox: SandboxInfo) -> None:
        # M1.14: a stop keeps files and transcripts but kills processes, and the leftover marker
        # would make start-team a no-op. start_team() clears the marker itself, so restarting is
        # releasing the dead holder and starting again. Whether seats resume their earlier
        # conversation (claude --resume) is NOT verified yet.
        self._release(sandbox.name)
        await self.start_team(sandbox)

    async def wake_role(self, sandbox: SandboxInfo, role: str, fallback: bool = False) -> None:
        cmd = "crew-notify-fallback" if fallback else "crew-notify"
        await self._run(["exec", sandbox.name, cmd, role])

    async def stop(self, sandbox: SandboxInfo) -> SandboxInfo:
        self._release(sandbox.name)
        async with self._sessionmaker() as session:
            row = await session.scalar(select(Sandbox).where(Sandbox.id == sandbox.id))
            if row is None:
                raise SandboxProviderError(f"no sandbox row for id={sandbox.id}")
            if row.status != "stopped":
                await self._run(["env", "stop", sandbox.name])  # *unverified subcommand name
                row.status = "stopped"
            await session.commit()
            await session.refresh(row)
            return _sandbox_info(row)

    async def remove(self, sandbox: SandboxInfo) -> SandboxInfo:
        self._release(sandbox.name)
        async with self._sessionmaker() as session:
            row = await session.scalar(select(Sandbox).where(Sandbox.id == sandbox.id))
            if row is None:
                raise SandboxProviderError(f"no sandbox row for id={sandbox.id}")
            # "rm" verbatim, "--auto-approve" extrapolated from create's flag
            await self._run(["env", "rm", sandbox.name, "--auto-approve"])
            row.status = "removed"
            row.closed_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(row)
            return _sandbox_info(row)

    async def reconcile(self) -> list[SandboxDrift]:
        result = await self._run(["ls", "--json"])  # *unverified: flag/shape not confirmed
        live = _parse_sbx_ls(result.stdout)

        async with self._sessionmaker() as session:
            db_rows = (await session.execute(
                select(Sandbox).where(Sandbox.status != "removed")
            )).scalars().all()
            row_by_name = {row.name: row for row in db_rows}
            snapshots = [SandboxRow(task_id=row.task_id, name=row.name, status=row.status) for row in db_rows]

            drifts = _diff(live, snapshots)

            now = datetime.now(timezone.utc)
            for drift in drifts:
                if drift.resolution == "marked_removed":
                    row = row_by_name[drift.name]
                    row.status = "removed"
                    row.closed_at = now
                elif drift.resolution == "synced":
                    row = row_by_name[drift.name]
                    if row.status != drift.live_status:
                        row.status = drift.live_status
                # orphan_reported: reported only, never auto-removed -- no destructive
                # action without a human/coordinator in the loop.

            await session.commit()

        return drifts
