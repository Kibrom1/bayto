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
Update 2026-10-18: creating the sandbox (through dev-team/run.sh), starting the team and the seat turns are proven
by live Build sessions (docs/e2e/live/live_build_e2e.py). Still unconfirmed: the `ls --json` schema used by
reconcile, and the `env stop` spelling.
Verify all of the above from a host with the real `sbx` CLI before relying on this in
production; until then, treat `SubprocessSbxRunner` as unverified glue and `LocalSbxSandboxProvider`
as verified only at the DB/argv-shape level (see tests/test_local_sandbox.py).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Mapping
from typing import AsyncIterator, Iterator, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..child_env import child_env
from ..models import Sandbox
from ..providers.gateway import check_override, redact
from .provider import (
    SandboxCommandError,
    SandboxDrift,
    SandboxInfo,
    SandboxProvider,
    SandboxProviderError,
)
from .turns import TextDelta, ToolUse, TurnEvent, TurnResult, parse_stream_line

log = logging.getLogger(__name__)

PROVIDER_NAME = "local-sbx"

# The team lives as long as the sandbox-side exec does; killing the `sbx exec` CLIENT on the host does
# NOT end it (live check, 2026-10-08: seats kept running after the client was terminated). So the exec
# records its own PID, and the team is adopted or released through that PID, not through the client.
TEAM_DIR = "$HOME/work/factory"
HOLDER_PID = TEAM_DIR + "/team-holder.pid"
# Clear a stale marker (a leftover one makes start-team exit at once and start nothing), record the PID,
# start the team, then keep this exec alive: the team dies when the exec that started it ends.
TEAM_SCRIPT = f"rm -f {TEAM_DIR}/team-started; echo $$ > {HOLDER_PID}; start-team; sleep infinity"
# One headless turn for a role (positional $1). Mirrors how start-team launches a seat: the role's own
# tool-flag file (one argv element per line; refuses to run a role with none), its model from team.tsv, the
# workspace as an extra dir, and the role brief as the system prompt. The prompt arrives on stdin.
TURN_SCRIPT = r'''set -euo pipefail
role="$1"
export WORKSPACE_DIR="$HOME/work" FACTORY_DIR="$HOME/work/factory" PATH="$HOME/work/bin:$HOME/.local/bin:$PATH" FACTORY_ROLE="$role"
cd "$WORKSPACE_DIR/app"
[ -s "$FACTORY_DIR/tool-flags/$role" ] || { echo "no tool-permission flags for $role; refusing to run it unrestricted" >&2; exit 2; }
model="$(awk -v r="$role" '$1==r {print $4}' "$WORKSPACE_DIR/team.tsv")"
args=(--model "$model")
while IFS= read -r flag; do args+=("$flag"); done < "$FACTORY_DIR/tool-flags/$role"
exec claude -p "${args[@]}" --add-dir "$WORKSPACE_DIR" --append-system-prompt "$(cat "$WORKSPACE_DIR/roles/$role.md")" \
  --output-format stream-json --verbose --include-partial-messages
'''
# The same turn for ONE seat that needs its own model name and environment (a seat on a gateway model). The host cannot
# hand environment variables to a process inside the sandbox through `sbx exec` (unverified), and putting a token in the
# command line would show it in `ps` and in error messages, so the values arrive on stdin, ahead of the prompt: a line
# with the number of variables, then that many NAME=value lines (only ANTHROPIC_* names are accepted). An ANTHROPIC_API_KEY
# already in the sandbox is dropped first so it cannot be sent to the gateway. The model is the second argument.
_OVERRIDE_HEADER = r'''unset ANTHROPIC_API_KEY
IFS= read -r count
for ((i = 0; i < count; i++)); do
  IFS= read -r kv
  case "${kv%%=*}" in ANTHROPIC_[A-Z_]*) export "$kv" ;; *) echo "refusing a per-seat variable that is not ANTHROPIC_*" >&2; exit 2 ;; esac
done
'''
_AWK_MODEL = r'''"$(awk -v r="$role" '$1==r {print $4}' "$WORKSPACE_DIR/team.tsv")"'''
TURN_SCRIPT_OVERRIDE = TURN_SCRIPT.replace('role="$1"\n', 'role="$1"\n' + _OVERRIDE_HEADER, 1).replace(
    "model=" + _AWK_MODEL, 'model="${2:-$(awk -v r="$role" \'$1==r {print $4}\' "$WORKSPACE_DIR/team.tsv")}"', 1)
assert TURN_SCRIPT_OVERRIDE.count("read -r count") == 1 and "${2:-$(awk" in TURN_SCRIPT_OVERRIDE
TEAM_READY_PROBE = f'test -f "{TEAM_DIR}/team-started"'
# Exit 0 only if a holder from an earlier start is still alive and the team came up.
TEAM_ALIVE_PROBE = f'p=$(cat "{HOLDER_PID}" 2>/dev/null) && [ -n "$p" ] && kill -0 "$p" && test -f "{TEAM_DIR}/team-started"'
# Stop the team explicitly. start-team runs `nohup herdr server &`, so the herdr server and every seat
# are detached from the exec that started them: ending the holder alone leaves them running (live check,
# 2026-10-08: a restart then failed with "agent name coordinator is already used"). So: end the holder,
# kill every process that carries a FACTORY_ROLE (the seats), kill the herdr server, clear the markers.
TEAM_KILL = (
    f'p=$(cat "{HOLDER_PID}" 2>/dev/null); [ -n "$p" ] && {{ pkill -P "$p"; kill "$p"; }}; '
    'for d in /proc/[0-9]*; do pid=${d#/proc/}; [ "$pid" = "$$" ] && continue; '
    'tr "\\0" "\\n" 2>/dev/null < $d/environ | grep -q "^FACTORY_ROLE=" && kill "$pid"; done; '
    'pkill -f "[h]erdr server"; sleep 2; '
    f'rm -f "{HOLDER_PID}" "{TEAM_DIR}/team-started"; true'
)


class HeldExec(Protocol):
    """A long-lived `sbx exec` the provider keeps open. Processes started inside a sandbox by
    `sbx exec` die when that exec returns (M1.12), so the team lives exactly as long as this does."""

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def log_tail(self) -> str: ...


class SbxRunner(Protocol):
    def run(self, argv: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]: ...
    def start(self, argv: list[str]) -> HeldExec: ...
    def stream(self, argv: list[str], *, stdin: str) -> Iterator[str]: ...


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


class _LineStream:
    """stdout lines of a subprocess. A generator cannot be closed while another thread is inside its
    `next()` ("generator already executing"), and cancelling a turn is exactly that situation, so this is a
    plain iterator with a thread-safe `kill()`."""

    def __init__(self, cmd: list[str], argv: list[str], stdin: str) -> None:
        self._argv = argv
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True, bufsize=1, env=child_env())
        assert self._proc.stdin is not None and self._proc.stdout is not None
        try:
            self._proc.stdin.write(stdin)
            self._proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass  # the process already exited; its exit code is reported by __next__
        self._done = False

    def __iter__(self) -> "_LineStream":
        return self

    def __next__(self) -> str:
        if self._done:
            raise StopIteration
        line = self._proc.stdout.readline()  # type: ignore[union-attr]
        if line:
            return line
        self._done = True
        err = self._proc.stderr.read() if self._proc.stderr else ""
        code = self._proc.wait()
        if code != 0:
            raise SandboxCommandError(self._argv, code, err)
        raise StopIteration

    def kill(self) -> None:
        try:
            if self._proc.poll() is None:
                self._proc.kill()
        except OSError:
            pass

    def close(self) -> None:
        self.kill()
        try:
            self._proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        for f in (self._proc.stdout, self._proc.stderr):
            try:
                f and f.close()
            except OSError:
                pass


class SubprocessSbxRunner:
    """Real `sbx` CLI invocation. Unverified: see module docstring."""

    def run(self, argv: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["sbx", *argv], capture_output=True, text=True, timeout=timeout, check=False, env=child_env())

    def stream(self, argv: list[str], *, stdin: str) -> Iterator[str]:
        """Run `sbx <argv>` with `stdin` as input and yield stdout lines as they arrive. Raises
        SandboxCommandError if the process exits non-zero. The returned stream can be `kill()`ed from any
        thread (which makes a blocked `next()` return) and `close()`d once no thread is inside `next()`."""
        return _LineStream(["sbx", *argv], argv, stdin)

    def start(self, argv: list[str]) -> HeldExec:
        log = tempfile.NamedTemporaryFile(prefix="sbx-held-", suffix=".log", delete=False)
        proc = subprocess.Popen(["sbx", *argv], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=child_env())
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
    """Parses `sbx ls --json` output into {name: status}. The real shape is still UNVERIFIED, so
    this accepts what the CLI is known or likely to print instead of crashing startup: a JSON list
    of objects, or an object wrapping such a list (e.g. {"sandboxes": [...]}), with the name and
    status under "name"/"status" (any capitalisation, or "state"). Empty output, plain text such as
    "No sandboxes found." and an empty wrapper all mean no live sandboxes."""
    text = stdout.strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        data = lists[0] if lists else []
    live: dict[str, str] = {}
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict):
            continue
        fields = {str(k).lower(): v for k, v in entry.items()}
        name = fields.get("name")
        if name:
            live[str(name)] = str(fields.get("status") or fields.get("state") or "")
    return live


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


# The product's sandbox definition lives in the repo (envs/team.sbxenv.yaml). A bare relative path only
# worked when the orchestrator happened to run from that folder, so resolve it from this file instead.
DEFAULT_SBXENV = Path(os.environ.get("BAYTO_SBXENV") or Path(__file__).resolve().parents[4] / "envs" / "team.sbxenv.yaml")


class LocalSbxSandboxProvider(SandboxProvider):
    def __init__(self, sessionmaker: async_sessionmaker, *, runner: SbxRunner | None = None,
                 sbxenv_path: Path = DEFAULT_SBXENV,
                 team_ready_timeout: float = 900.0, team_poll_interval: float = 5.0,
                 create_timeout: float = float(os.environ.get("BAYTO_SBX_CREATE_TIMEOUT", "900")),
                 provisioner=None) -> None:
        # provisioner: async (name, seats) -> None that builds the whole team sandbox (see provision.py).
        # Without one, create() only runs `sbx env create`, which gives an empty sandbox with no team tools.
        self._provisioner = provisioner
        self._create_timeout = create_timeout
        self._sessionmaker = sessionmaker
        self._runner = runner or SubprocessSbxRunner()
        self._sbxenv_path = sbxenv_path
        # 8 agents took 437 s in M1.12 and 512 s in the 2026-10-08 live check; 900 s leaves headroom.
        self._team_ready_timeout = team_ready_timeout
        self._team_poll_interval = team_poll_interval
        self._held: dict[str, HeldExec] = {}
        # Streamed turns are opt-in until they have been checked on a live team (m5-turn-live.sh).
        self.supports_streaming = os.environ.get("BAYTO_STREAMING_TURNS") == "1"

    def _drop_client(self, name: str) -> None:
        """Forget and terminate the host-side `sbx exec` client. This does NOT stop the team."""
        held = self._held.pop(name, None)
        if held is not None and held.poll() is None:
            held.terminate()

    async def release_team(self, sandbox: SandboxInfo) -> None:
        """Stop the team: end the sandbox-side holder (best effort; the sandbox may already be stopped)
        and drop the host-side client."""
        try:
            await asyncio.to_thread(self._runner.run, ["exec", sandbox.name, "bash", "-lc", TEAM_KILL])
        except Exception:
            pass
        self._drop_client(sandbox.name)

    async def close(self) -> None:
        """Orchestrator shutdown: drop the host-side clients only. Teams keep running in their sandboxes
        (a sandbox stops only on user action) and the next start_team() adopts them."""
        for name in list(self._held):
            self._drop_client(name)

    async def _run(self, argv: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        try:
            result = await asyncio.to_thread(self._runner.run, argv, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise SandboxProviderError(
                f"`sbx {' '.join(argv[:2])}` did not finish within {timeout:.0f} s "
                f"(still running or waiting for input); check `sbx` by hand") from exc
        if result.returncode != 0:
            raise SandboxCommandError(argv, result.returncode, result.stderr)
        return result

    def _rm_argv(self, name: str) -> list[str]:
        """`sbx env rm` takes the PATH of the environment file the sandbox was created from, never a name
        (live runs 2026-10-22/23: a bare name was read as a folder, and --auto-approve is create-only). The name goes
        in as the env arg, and --force skips the prompt. A team built by dev-team/run.sh was created from the
        file it generated; otherwise it is the repo's envs/team.sbxenv.yaml."""
        built = Path(__file__).resolve().parents[4] / "dev-team" / ".build" / "factory" / "sbxenv.yaml"
        env_file = built if (self._provisioner is not None and built.exists()) else self._sbxenv_path
        return ["env", "rm", str(env_file), "--env-arg", f"name={name}", "--force"]

    async def create(self, task_id: uuid.UUID, *, name: str, seats=None, workspace: str | None = None) -> SandboxInfo:
        async with self._sessionmaker() as session:
            row = await session.scalar(select(Sandbox).where(Sandbox.task_id == task_id))
            if row is None or row.status not in ("running", "creating"):
                async def build() -> None:
                    if self._provisioner is not None and seats:
                        if workspace:
                            await self._provisioner(name, seats, workspace=workspace)
                        else:
                            await self._provisioner(name, seats)
                    else:
                        await self._run([
                            "env", "create", str(self._sbxenv_path), "--env-arg", f"name={name}", "--auto-approve",
                        ], timeout=self._create_timeout)

                try:
                    await build()
                except Exception as exc:
                    # One sandbox per task. After a stop, a failed start or an orchestrator restart the Postgres row
                    # can say "not running" while the sandbox still exists in sbx, so a plain create collides with
                    # it. The sandbox is ours (named for this task and not live), so remove it and create it again.
                    # The "remove it with `sbx env rm`" wording is sbx's own message (2026-10-19 live run).
                    if "already exists" not in str(exc):
                        raise
                    log.warning("sandbox %s already exists but is not live; removing it and creating it again", name)
                    self._drop_client(name)
                    await self._run(self._rm_argv(name))
                    await build()
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
        """Make sure the team is running, and return once it is up.

        If a holder is already alive (here, or left by an earlier orchestrator process: the sandbox
        side is checked by PID) the team is adopted and nothing is started, so a relaunch never
        starts a second set of seats. Otherwise the team is started under a held-open exec and we
        wait until `team-started` appears. Raises SandboxProviderError if the exec exits early or the
        team is not up in time; the exec is then ended."""
        name = sandbox.name
        held = self._held.get(name)
        if held is not None and held.poll() is None:
            return
        self._drop_client(name)
        alive = await asyncio.to_thread(self._runner.run, ["exec", name, "bash", "-lc", TEAM_ALIVE_PROBE])
        if alive.returncode == 0:
            return  # adopt the running team
        held = self._runner.start(["exec", name, "bash", "-lc", TEAM_SCRIPT])
        self._held[name] = held
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._team_ready_timeout
        while True:
            code = held.poll()
            if code is not None:
                tail = held.log_tail()
                self._drop_client(name)
                raise SandboxProviderError(f"start-team exec for {name} exited with {code} before the team was up: {tail}")
            probe = await asyncio.to_thread(self._runner.run, ["exec", name, "bash", "-lc", TEAM_READY_PROBE])
            if probe.returncode == 0:
                return
            if loop.time() >= deadline:
                tail = held.log_tail()
                await self.release_team(sandbox)
                raise SandboxProviderError(
                    f"team-started not seen in {name} after {self._team_ready_timeout:.0f} s: {tail}")
            await asyncio.sleep(self._team_poll_interval)

    async def restart_team(self, sandbox: SandboxInfo) -> None:
        # M1.14: a stop keeps files and transcripts but kills processes. Also used to replace a team that
        # is still running. End any old holder first (otherwise start_team would adopt it), then start.
        # Whether seats resume their earlier conversation (claude --resume) is NOT verified yet.
        await self.release_team(sandbox)
        await self.start_team(sandbox)

    async def run_turn(self, sandbox: SandboxInfo, role: str, prompt: str, *, env: Mapping[str, str] | None = None,
                       model: str | None = None) -> AsyncIterator[TurnEvent]:
        stdin = prompt
        if env or model:
            # Per-seat override (gateway seat): values go in on stdin, never in argv; see TURN_SCRIPT_OVERRIDE.
            env = dict(env or {})
            check_override(env)
            argv = ["exec", "-i", sandbox.name, "bash", "-lc", TURN_SCRIPT_OVERRIDE, "_", role, *([model] if model else [])]
            stdin = "\n".join([str(len(env)), *(f"{k}={v}" for k, v in env.items())]) + "\n" + prompt
        else:
            argv = ["exec", "-i", sandbox.name, "bash", "-lc", TURN_SCRIPT, "_", role]
        lines = iter(self._runner.stream(argv, stdin=stdin))
        loop = asyncio.get_running_loop()
        pending: asyncio.Future | None = None
        saw_result = False
        try:
            while True:
                pending = loop.run_in_executor(None, next, lines, None)
                # shield: cancelling this coroutine must not abandon the worker thread mid-read
                try:
                    line = await asyncio.shield(pending)
                except SandboxCommandError as exc:
                    if not env:
                        raise
                    # The child's stderr can echo its environment; the token must not reach logs or the room.
                    raise SandboxCommandError(exc.argv, exc.returncode, redact(exc.stderr, env)) from None
                pending = None
                if line is None:
                    break
                event = parse_stream_line(line)
                if event is None:
                    continue
                if isinstance(event, TurnResult):
                    saw_result = True
                yield event
        finally:
            # Cancelled (Pause/Resume/Stop) or finished: end the subprocess FIRST so a thread blocked in
            # next() returns, wait for it, and only then close. Closing a generator that another thread is
            # running raised "generator already executing", which replaced the cancellation and let a
            # cancelled runner carry on as a second runner.
            kill = getattr(lines, "kill", None)
            if kill is not None:
                kill()
            if pending is not None:
                await asyncio.gather(pending, return_exceptions=True)
            close = getattr(lines, "close", None)
            if close is not None:
                try:
                    await asyncio.to_thread(close)
                except ValueError:
                    pass  # a plain generator still mid-next(); nothing more to release
        if not saw_result:
            raise SandboxProviderError(f"turn for {role} in {sandbox.name} ended without a result event")

    async def wake_role(self, sandbox: SandboxInfo, role: str) -> None:
        try:
            await self._run(["exec", sandbox.name, "crew-notify", role])
        except SandboxCommandError as exc:
            # A second poke for an assignment the seat was already notified about (a resumed or retried
            # grant) is harmless -- the reply is on its way -- not a failure that should end the session.
            if "already notified" not in (exc.stderr or ""):
                raise

    async def stop(self, sandbox: SandboxInfo) -> SandboxInfo:
        self._drop_client(sandbox.name)
        async with self._sessionmaker() as session:
            row = await session.scalar(select(Sandbox).where(Sandbox.id == sandbox.id))
            if row is None:
                raise SandboxProviderError(f"no sandbox row for id={sandbox.id}")
            if row.status != "stopped":
                await self.release_team(sandbox)  # end the sandbox-side holder first
                await self._run(["env", "stop", sandbox.name])  # *unverified subcommand name
                row.status = "stopped"
            await session.commit()
            await session.refresh(row)
            return _sandbox_info(row)

    async def remove(self, sandbox: SandboxInfo) -> SandboxInfo:
        await self.release_team(sandbox)
        async with self._sessionmaker() as session:
            row = await session.scalar(select(Sandbox).where(Sandbox.id == sandbox.id))
            if row is None:
                raise SandboxProviderError(f"no sandbox row for id={sandbox.id}")
            await self._run(self._rm_argv(sandbox.name))
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
