"""M2.4: LocalSbxSandboxProvider against a FakeSbxRunner (no real `sbx` binary needed --
none is available in this dev-team sandbox). Covers: each lifecycle method builds the argv
the architect's design specifies, a nonzero returncode raises SandboxCommandError and
leaves the DB row unmodified, create() is idempotent, and reconcile() applies drift to
real rows. DB assertions run against a real, throwaway Postgres via the shared
`live_sessionmaker` fixture in conftest.py (skips cleanly if none is reachable), same as
M2.2/M2.3.
"""
import json
import subprocess
import uuid

import pytest
from sqlalchemy import select

from orchestrator.models import Sandbox, Task
from orchestrator.sandbox.local import LocalSbxSandboxProvider
from orchestrator.sandbox.provider import SandboxCommandError, SandboxInfo, SandboxProviderError


class FakeHeld:
    def __init__(self):
        self.exit_code: int | None = None
        self.terminated = False

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.terminated = True
        self.exit_code = -15

    def log_tail(self):
        return "fake log"


class FakeSbxRunner:
    """Records every argv it was called with; returns a pre-programmed
    CompletedProcess keyed by the leading subcommand (`env create`/`env stop`/`env rm`,
    `exec start-team`/`exec crew-notify`, or `ls`)."""

    def __init__(self, responses: dict[str, subprocess.CompletedProcess] | None = None):
        self.calls: list[list[str]] = []
        self._responses = responses or {}
        self.started: list[list[str]] = []
        self.helds: list[FakeHeld] = []
        self.probes = 0
        self.team_ready_after: int | None = 0  # probe number (0-based) at which team-started appears

    @staticmethod
    def _key(argv: list[str]) -> str:
        if argv[0] == "env":
            return f"env {argv[1]}"
        if argv[0] == "exec":
            return f"exec {argv[2]}"
        return argv[0]

    def start(self, argv):
        self.started.append(argv)
        held = FakeHeld()
        self.helds.append(held)
        return held

    def run(self, argv, *, timeout=None):
        self.calls.append(argv)
        if argv[:2] == ["exec", argv[1]] and argv[2:3] == ["bash"] and "team-started" in argv[-1] and "test -f" in argv[-1]:
            ready = self.team_ready_after is not None and self.probes >= self.team_ready_after
            self.probes += 1
            return subprocess.CompletedProcess(argv, returncode=0 if ready else 1, stdout="", stderr="")
        return self._responses.get(
            self._key(argv), subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr=""),
        )


async def _make_task(sessionmaker) -> uuid.UUID:
    async with sessionmaker() as session:
        task = Task(title="wad-102", output_type="text")
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task.id


async def test_create_builds_expected_argv_and_inserts_a_row(live_sessionmaker):
    runner = FakeSbxRunner()
    provider = LocalSbxSandboxProvider(live_sessionmaker, runner=runner)
    task_id = await _make_task(live_sessionmaker)

    info = await provider.create(task_id, name="sbx-wad-102")

    assert runner.calls == [
        ["env", "create", "team.sbxenv.yaml", "--env-arg", "name=sbx-wad-102", "--auto-approve"],
    ]
    assert info.task_id == task_id
    assert info.name == "sbx-wad-102"
    assert info.status == "running"
    assert info.provider == "local-sbx"

    async with live_sessionmaker() as db:
        row = await db.scalar(select(Sandbox).where(Sandbox.task_id == task_id))
    assert row is not None and row.status == "running"


async def test_create_is_idempotent_when_already_running(live_sessionmaker):
    runner = FakeSbxRunner()
    provider = LocalSbxSandboxProvider(live_sessionmaker, runner=runner)
    task_id = await _make_task(live_sessionmaker)

    first = await provider.create(task_id, name="sbx-wad-102")
    second = await provider.create(task_id, name="sbx-wad-102")

    assert len(runner.calls) == 1  # the second call did not re-invoke the runner
    assert second.id == first.id
    assert second.status == "running"


async def test_create_reissues_command_when_existing_row_is_not_running_or_creating(live_sessionmaker):
    runner = FakeSbxRunner()
    provider = LocalSbxSandboxProvider(live_sessionmaker, runner=runner)
    task_id = await _make_task(live_sessionmaker)

    await provider.create(task_id, name="sbx-wad-102")
    await provider.stop(await provider.create(task_id, name="sbx-wad-102"))

    calls_before = len(runner.calls)
    again = await provider.create(task_id, name="sbx-wad-102")

    assert len(runner.calls) == calls_before + 1  # re-issued because the row was "stopped"
    assert again.status == "running"


def _team_provider(runner, **kw):
    return LocalSbxSandboxProvider(sessionmaker=None, runner=runner, team_poll_interval=0, **kw)


async def test_start_team_holds_the_exec_open_and_waits_for_the_marker():
    runner = FakeSbxRunner()
    runner.team_ready_after = 2  # the third probe sees team-started
    provider = _team_provider(runner)

    await provider.start_team(_fake_info(name="sbx-wad-102"))

    assert runner.started == [[
        "exec", "sbx-wad-102", "bash", "-lc",
        "rm -f $HOME/work/factory/team-started; start-team; sleep infinity",
    ]]
    assert runner.probes == 3
    assert runner.helds[0].terminated is False  # still held: the team lives as long as it does


async def test_start_team_is_a_noop_when_a_live_holder_exists():
    runner = FakeSbxRunner()
    provider = _team_provider(runner)
    sandbox = _fake_info(name="sbx-wad-102")

    await provider.start_team(sandbox)
    await provider.start_team(sandbox)

    assert len(runner.started) == 1


async def test_start_team_raises_and_releases_when_the_holder_exits_early():
    runner = FakeSbxRunner()
    runner.team_ready_after = None
    provider = _team_provider(runner)
    sandbox = _fake_info(name="sbx-wad-102")
    real_start = runner.start

    def start_and_die(argv):
        held = real_start(argv)
        held.exit_code = 1
        return held

    runner.start = start_and_die
    with pytest.raises(SandboxProviderError, match="exited with 1"):
        await provider.start_team(sandbox)
    assert "sbx-wad-102" not in provider._held


async def test_start_team_times_out_and_releases_the_holder():
    runner = FakeSbxRunner()
    runner.team_ready_after = None  # never appears
    provider = _team_provider(runner, team_ready_timeout=0.0)

    with pytest.raises(SandboxProviderError, match="team-started not seen"):
        await provider.start_team(_fake_info(name="sbx-wad-102"))

    assert runner.helds[0].terminated is True
    assert provider._held == {}


async def test_restart_team_releases_the_old_holder_and_starts_a_new_one():
    runner = FakeSbxRunner()
    provider = _team_provider(runner)
    sandbox = _fake_info(name="sbx-wad-102")
    await provider.start_team(sandbox)

    await provider.restart_team(sandbox)

    assert len(runner.started) == 2
    assert runner.helds[0].terminated is True
    assert runner.helds[1].terminated is False


async def test_close_releases_every_held_exec():
    runner = FakeSbxRunner()
    provider = _team_provider(runner)
    await provider.start_team(_fake_info(name="a"))
    await provider.start_team(_fake_info(name="b"))

    await provider.close()

    assert all(h.terminated for h in runner.helds)
    assert provider._held == {}


async def test_wake_role_builds_expected_argv():
    runner = FakeSbxRunner()
    provider = LocalSbxSandboxProvider(sessionmaker=None, runner=runner)
    sandbox = _fake_info(name="sbx-wad-102")

    await provider.wake_role(sandbox, "backend-engineer")

    assert runner.calls == [["exec", "sbx-wad-102", "crew-notify", "backend-engineer"]]


async def test_stop_builds_expected_argv_and_updates_status(live_sessionmaker):
    runner = FakeSbxRunner()
    provider = LocalSbxSandboxProvider(live_sessionmaker, runner=runner)
    task_id = await _make_task(live_sessionmaker)
    created = await provider.create(task_id, name="sbx-wad-102")

    stopped = await provider.stop(created)

    assert runner.calls[-1] == ["env", "stop", "sbx-wad-102"]
    assert stopped.status == "stopped"


async def test_stop_releases_the_held_exec(live_sessionmaker):
    runner = FakeSbxRunner()
    provider = LocalSbxSandboxProvider(live_sessionmaker, runner=runner, team_poll_interval=0)
    task_id = await _make_task(live_sessionmaker)
    info = await provider.create(task_id, name="sbx-wad-102")
    await provider.start_team(info)

    await provider.stop(info)

    assert runner.helds[0].terminated is True


async def test_stop_is_a_noop_when_already_stopped(live_sessionmaker):
    runner = FakeSbxRunner()
    provider = LocalSbxSandboxProvider(live_sessionmaker, runner=runner)
    task_id = await _make_task(live_sessionmaker)
    created = await provider.create(task_id, name="sbx-wad-102")
    await provider.stop(created)

    calls_before = len(runner.calls)
    await provider.stop(created)

    assert len(runner.calls) == calls_before  # no second "env stop" issued


async def test_remove_builds_expected_argv_and_updates_status(live_sessionmaker):
    runner = FakeSbxRunner()
    provider = LocalSbxSandboxProvider(live_sessionmaker, runner=runner)
    task_id = await _make_task(live_sessionmaker)
    created = await provider.create(task_id, name="sbx-wad-102")

    removed = await provider.remove(created)

    assert runner.calls[-1] == ["env", "rm", "sbx-wad-102", "--auto-approve"]
    assert removed.status == "removed"
    assert removed.closed_at is not None


async def test_failed_command_raises_and_leaves_row_unmodified(live_sessionmaker):
    runner = FakeSbxRunner({"env stop": subprocess.CompletedProcess([], returncode=1, stdout="", stderr="boom")})
    provider = LocalSbxSandboxProvider(live_sessionmaker, runner=runner)
    task_id = await _make_task(live_sessionmaker)
    created = await provider.create(task_id, name="sbx-wad-102")

    with pytest.raises(SandboxCommandError) as exc_info:
        await provider.stop(created)
    assert exc_info.value.argv == ["env", "stop", "sbx-wad-102"]
    assert exc_info.value.returncode == 1
    assert exc_info.value.stderr == "boom"

    async with live_sessionmaker() as db:
        row = await db.scalar(select(Sandbox).where(Sandbox.id == created.id))
    assert row.status == "running"  # unchanged despite the failed stop


async def test_reconcile_marks_removed_reports_orphans_and_syncs_status(live_sessionmaker):
    runner = FakeSbxRunner()
    provider = LocalSbxSandboxProvider(live_sessionmaker, runner=runner)

    task_synced = await _make_task(live_sessionmaker)
    task_gone = await _make_task(live_sessionmaker)
    synced = await provider.create(task_synced, name="sbx-synced")
    gone = await provider.create(task_gone, name="sbx-gone")

    ls_payload = json.dumps([
        {"name": "sbx-synced", "status": "running"},
        {"name": "sbx-orphan", "status": "running"},
    ])
    runner._responses["ls"] = subprocess.CompletedProcess([], returncode=0, stdout=ls_payload, stderr="")

    drifts = await provider.reconcile()

    by_name = {d.name: d for d in drifts}
    assert by_name["sbx-synced"].resolution == "synced"
    assert by_name["sbx-gone"].resolution == "marked_removed"
    assert by_name["sbx-orphan"].resolution == "orphan_reported"
    assert by_name["sbx-orphan"].task_id is None

    async with live_sessionmaker() as db:
        synced_row = await db.scalar(select(Sandbox).where(Sandbox.id == synced.id))
        gone_row = await db.scalar(select(Sandbox).where(Sandbox.id == gone.id))
    assert synced_row.status == "running"
    assert gone_row.status == "removed"
    assert gone_row.closed_at is not None
    # An orphan is only reported, never auto-removed: no matching DB row was created for it.


def _fake_info(*, name: str) -> SandboxInfo:
    """A SandboxInfo for methods that only read `sandbox.name` and don't touch the DB
    (start_team/wake_role) -- no need for a real row."""
    return SandboxInfo(
        id=uuid.uuid4(), task_id=uuid.uuid4(), provider="local-sbx", name=name,
        status="running", image=None, created_at=None, closed_at=None,
    )
