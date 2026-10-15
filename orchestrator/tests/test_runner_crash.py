"""A moderator runner that dies must mark its session failed, with the reason, instead of leaving it
`active` forever with nobody told (found when the shipped modes crashed every runner on its first
turn and the session just sat there)."""
import asyncio

import pytest

from orchestrator.api import runtime
from orchestrator.models import Session
from test_api_sessions import _seed


async def _session(live_sessionmaker, status):
    task_id, mode_id, _ = await _seed(live_sessionmaker)
    async with live_sessionmaker() as db:
        row = Session(task_id=task_id, mode_id=mode_id, status=status)
        db.add(row)
        await db.commit()
        return row.id


async def _crash(live_sessionmaker, session_id, exc):
    async def boom():
        raise exc
    t = asyncio.create_task(boom())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    runtime._on_runner_done(t, session_id, live_sessionmaker)
    await asyncio.gather(*list(runtime._failure_tasks))


async def _status(live_sessionmaker, session_id):
    async with live_sessionmaker() as db:
        return (await db.get(Session, session_id)).status


async def test_a_crashed_runner_marks_an_active_session_failed(live_sessionmaker):
    sid = await _session(live_sessionmaker, "active")
    await _crash(live_sessionmaker, sid, RuntimeError("kaboom in /home/kb/secret/place"))
    assert await _status(live_sessionmaker, sid) == "failed"
    reason = runtime.session_failures[sid]
    assert "kaboom" in reason and "RuntimeError" in reason
    assert "/home/kb" not in reason  # scrubbed like every other failure reason


@pytest.mark.parametrize("final", ["finished", "cancelled", "failed"])
async def test_a_crash_never_overwrites_a_final_status(live_sessionmaker, final):
    sid = await _session(live_sessionmaker, final)
    await _crash(live_sessionmaker, sid, RuntimeError("late crash"))
    assert await _status(live_sessionmaker, sid) == final


async def test_a_clean_finish_or_cancel_is_ignored(live_sessionmaker):
    sid = await _session(live_sessionmaker, "active")

    async def fine():
        return None
    t = asyncio.create_task(fine())
    await t
    runtime._on_runner_done(t, sid, live_sessionmaker)
    assert await _status(live_sessionmaker, sid) == "active"
