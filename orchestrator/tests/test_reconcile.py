"""M2.9: reconcile_on_startup()'s selection logic -- which sessions it relaunches, which
it orphans, per-session failure isolation. `launch_runner` is a spy/fake here (a plain
async callable), not a real ModeratorRunner -- that end-to-end resume behavior is
test_moderator_runner.py's job. Real Postgres for Task/Session/Sandbox rows; SandboxProvider
is FakeSandboxProvider (no live sbx CLI needed or assumed available -- same disclosed gap
as always, now also hit at startup).
"""
import uuid
from datetime import datetime, timezone

from orchestrator.models import Mode, Sandbox, Session, Task
from orchestrator.reconcile import STATUS_ORPHANED, reconcile_on_startup
from orchestrator.sandbox.provider import SandboxDrift

from fakes import FakeSandboxProvider


async def _make_task(sessionmaker) -> uuid.UUID:
    async with sessionmaker() as db:
        row = Task(title="t", output_type="text")
        db.add(row)
        await db.commit()
        return row.id


async def _make_sandbox(sessionmaker, task_id: uuid.UUID, *, status="running", name=None) -> str:
    name = name or f"sbx-{task_id}"
    async with sessionmaker() as db:
        db.add(Sandbox(task_id=task_id, provider="local-sbx", name=name, status=status,
                        created_at=datetime.now(timezone.utc)))
        await db.commit()
    return name


async def _make_session(sessionmaker, task_id: uuid.UUID, *, status: str, mode_name="m") -> uuid.UUID:
    async with sessionmaker() as db:
        mode = Mode(name=mode_name, phases_json={}, stop_rules_json={})
        db.add(mode)
        await db.flush()
        session_row = Session(task_id=task_id, mode_id=mode.id, status=status)
        db.add(session_row)
        await db.commit()
        return session_row.id


def _spy_launch_runner(calls: list[uuid.UUID], *, fail_for: set[uuid.UUID] | None = None):
    fail_for = fail_for or set()

    async def launch_runner(session_id: uuid.UUID) -> None:
        calls.append(session_id)
        if session_id in fail_for:
            raise RuntimeError(f"simulated launch failure for {session_id}")

    return launch_runner


async def _get_status(sessionmaker, session_id: uuid.UUID) -> str:
    async with sessionmaker() as db:
        return (await db.get(Session, session_id)).status


# ---------------------------------------------------------------- relaunch + orphan

async def test_relaunches_an_active_session_with_a_live_sandbox(live_sessionmaker):
    task_id = await _make_task(live_sessionmaker)
    name = await _make_sandbox(live_sessionmaker, task_id)
    session_id = await _make_session(live_sessionmaker, task_id, status="active")

    drift = [SandboxDrift(task_id=task_id, name=name, db_status="running", live_status="running",
                           resolution="synced")]
    calls: list[uuid.UUID] = []
    report = await reconcile_on_startup(FakeSandboxProvider(reconcile_result=drift), live_sessionmaker,
                                         _spy_launch_runner(calls))

    assert calls == [session_id]
    assert report.relaunched == [session_id]
    assert report.skipped_orphaned == []
    assert await _get_status(live_sessionmaker, session_id) == "active"  # untouched by reconcile itself


async def test_orphans_a_session_whose_sandbox_was_marked_removed(live_sessionmaker):
    task_id = await _make_task(live_sessionmaker)
    name = await _make_sandbox(live_sessionmaker, task_id)
    session_id = await _make_session(live_sessionmaker, task_id, status="active")

    drift = [SandboxDrift(task_id=task_id, name=name, db_status="running", live_status=None,
                           resolution="marked_removed")]
    calls: list[uuid.UUID] = []
    report = await reconcile_on_startup(FakeSandboxProvider(reconcile_result=drift), live_sessionmaker,
                                         _spy_launch_runner(calls))

    assert calls == []  # never attempted -- would fail unpredictably inside wake_role()
    assert report.skipped_orphaned == [session_id]
    status = await _get_status(live_sessionmaker, session_id)
    assert status == STATUS_ORPHANED
    assert status != "active"
    assert status != "failed"  # distinct from the generic explicit-run-failure status


async def test_orphans_a_session_whose_task_has_no_sandbox_row_at_all(live_sessionmaker):
    """No Sandbox row exists for this task -- reconcile() reports no drift for it either
    (not synced, not marked_removed, not an orphan_reported -- it's simply absent from
    both inputs to _diff). Still must not be relaunched: launch_runner() would fail
    unpredictably inside wake_role() with nothing to wake against."""
    task_id = await _make_task(live_sessionmaker)
    session_id = await _make_session(live_sessionmaker, task_id, status="active")

    calls: list[uuid.UUID] = []
    report = await reconcile_on_startup(FakeSandboxProvider(reconcile_result=[]), live_sessionmaker,
                                         _spy_launch_runner(calls))

    assert calls == []
    assert report.skipped_orphaned == [session_id]
    assert await _get_status(live_sessionmaker, session_id) == STATUS_ORPHANED


async def test_one_orphaned_session_does_not_affect_other_live_sessions_in_the_same_pass(live_sessionmaker):
    """The exact acceptance test product-owner asked for: one session's sandbox is
    missing; other live sessions in the same reconcile pass are unaffected."""
    gone_task = await _make_task(live_sessionmaker)
    gone_name = await _make_sandbox(live_sessionmaker, gone_task)
    gone_session = await _make_session(live_sessionmaker, gone_task, status="active")

    ok_task = await _make_task(live_sessionmaker)
    ok_name = await _make_sandbox(live_sessionmaker, ok_task)
    ok_session = await _make_session(live_sessionmaker, ok_task, status="active")

    drift = [
        SandboxDrift(task_id=gone_task, name=gone_name, db_status="running", live_status=None,
                     resolution="marked_removed"),
        SandboxDrift(task_id=ok_task, name=ok_name, db_status="running", live_status="running",
                     resolution="synced"),
    ]
    calls: list[uuid.UUID] = []
    report = await reconcile_on_startup(FakeSandboxProvider(reconcile_result=drift), live_sessionmaker,
                                         _spy_launch_runner(calls))

    assert calls == [ok_session]
    assert report.relaunched == [ok_session]
    assert report.skipped_orphaned == [gone_session]
    assert await _get_status(live_sessionmaker, gone_session) == STATUS_ORPHANED
    assert await _get_status(live_sessionmaker, ok_session) == "active"


# ---------------------------------------------------------------- per-session failure isolation

async def test_a_launch_failure_for_one_session_does_not_abort_the_others(live_sessionmaker):
    task_a = await _make_task(live_sessionmaker)
    name_a = await _make_sandbox(live_sessionmaker, task_a)
    session_a = await _make_session(live_sessionmaker, task_a, status="active")

    task_b = await _make_task(live_sessionmaker)
    name_b = await _make_sandbox(live_sessionmaker, task_b)
    session_b = await _make_session(live_sessionmaker, task_b, status="active")

    drift = [
        SandboxDrift(task_id=task_a, name=name_a, db_status="running", live_status="running", resolution="synced"),
        SandboxDrift(task_id=task_b, name=name_b, db_status="running", live_status="running", resolution="synced"),
    ]
    calls: list[uuid.UUID] = []
    report = await reconcile_on_startup(FakeSandboxProvider(reconcile_result=drift), live_sessionmaker,
                                         _spy_launch_runner(calls, fail_for={session_a}))

    assert set(calls) == {session_a, session_b}
    assert report.relaunched == [session_b]
    assert [sid for sid, _ in report.failed_to_launch] == [session_a]


# ---------------------------------------------------------------- status selection

async def test_created_and_finished_sessions_are_never_touched(live_sessionmaker):
    task_id = await _make_task(live_sessionmaker)
    created = await _make_session(live_sessionmaker, task_id, status="created")
    finished = await _make_session(live_sessionmaker, task_id, status="finished")

    calls: list[uuid.UUID] = []
    report = await reconcile_on_startup(FakeSandboxProvider(reconcile_result=[]), live_sessionmaker,
                                         _spy_launch_runner(calls))

    assert calls == []
    assert report.relaunched == report.skipped_orphaned == report.failed_to_launch == []
    assert await _get_status(live_sessionmaker, created) == "created"
    assert await _get_status(live_sessionmaker, finished) == "finished"


async def test_needs_human_session_is_left_untouched(live_sessionmaker):
    """A pause, not mid-flight work -- M2.7 builds no resume-from-human-decision path yet,
    so this is deliberately excluded from RESUMABLE_STATUSES."""
    task_id = await _make_task(live_sessionmaker)
    session_id = await _make_session(live_sessionmaker, task_id, status="needs_human")

    calls: list[uuid.UUID] = []
    report = await reconcile_on_startup(FakeSandboxProvider(reconcile_result=[]), live_sessionmaker,
                                         _spy_launch_runner(calls))

    assert calls == []
    assert await _get_status(live_sessionmaker, session_id) == "needs_human"


async def test_starting_session_with_no_sandbox_yet_is_relaunched_not_orphaned(live_sessionmaker):
    """A session stuck at "starting" may never have reached SandboxProvider.create() yet --
    that's an ordinary (idempotent) relaunch, not an orphan."""
    task_id = await _make_task(live_sessionmaker)
    session_id = await _make_session(live_sessionmaker, task_id, status="starting")

    calls: list[uuid.UUID] = []
    report = await reconcile_on_startup(FakeSandboxProvider(reconcile_result=[]), live_sessionmaker,
                                         _spy_launch_runner(calls))

    assert calls == [session_id]
    assert report.relaunched == [session_id]
    assert report.skipped_orphaned == []


async def test_stopping_and_cancelling_sessions_are_relaunched_to_wind_down(live_sessionmaker):
    task_id = await _make_task(live_sessionmaker)
    name = await _make_sandbox(live_sessionmaker, task_id)
    stopping = await _make_session(live_sessionmaker, task_id, status="stopping")
    task_id2 = await _make_task(live_sessionmaker)
    name2 = await _make_sandbox(live_sessionmaker, task_id2)
    cancelling = await _make_session(live_sessionmaker, task_id2, status="cancelling")

    drift = [
        SandboxDrift(task_id=task_id, name=name, db_status="running", live_status="running", resolution="synced"),
        SandboxDrift(task_id=task_id2, name=name2, db_status="running", live_status="running", resolution="synced"),
    ]
    calls: list[uuid.UUID] = []
    report = await reconcile_on_startup(FakeSandboxProvider(reconcile_result=drift), live_sessionmaker,
                                         _spy_launch_runner(calls))

    assert set(calls) == {stopping, cancelling}
    assert set(report.relaunched) == {stopping, cancelling}


# ---------------------------------------------------------------- reconcile() itself failing

async def test_sandbox_provider_reconcile_failure_does_not_orphan_everything(live_sessionmaker):
    """A transient SandboxProvider.reconcile() failure (e.g. no `sbx` CLI at all) must not
    be treated as "every sandbox is gone" -- that would be far more destructive than the
    actual failure warrants. Sessions still get an ordinary relaunch attempt."""
    task_id = await _make_task(live_sessionmaker)
    await _make_sandbox(live_sessionmaker, task_id)
    session_id = await _make_session(live_sessionmaker, task_id, status="active")

    calls: list[uuid.UUID] = []
    report = await reconcile_on_startup(FakeSandboxProvider(reconcile_raises=RuntimeError("no sbx binary")),
                                         live_sessionmaker, _spy_launch_runner(calls))

    assert calls == [session_id]
    assert report.relaunched == [session_id]
    assert report.skipped_orphaned == []
    assert await _get_status(live_sessionmaker, session_id) == "active"
