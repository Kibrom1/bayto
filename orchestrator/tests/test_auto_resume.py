import uuid
from datetime import datetime, timedelta, timezone

import pytest

from orchestrator import auto_resume

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    auto_resume._due.clear()
    monkeypatch.setenv(auto_resume.ENV, "1")
    yield
    auto_resume._due.clear()


def test_off_by_default(monkeypatch):
    monkeypatch.delenv(auto_resume.ENV)
    auto_resume.schedule(uuid.uuid4(), NOW)
    assert auto_resume.pending() == {}


async def test_a_session_is_resumed_once_after_its_reset_time():
    sid, calls = uuid.uuid4(), []

    async def resume(s):
        calls.append(s)
        return "ok"

    auto_resume.schedule(sid, NOW)
    assert await auto_resume.resume_due(resume, now=NOW) == []  # the buffer has not passed yet
    later = NOW + auto_resume.BUFFER + timedelta(seconds=1)
    assert await auto_resume.resume_due(resume, now=later) == [sid]
    assert await auto_resume.resume_due(resume, now=later + timedelta(hours=1)) == []
    assert calls == [sid] and auto_resume.pending() == {}


async def test_a_failed_attempt_is_retried_later_and_a_gone_session_is_dropped():
    a, b = uuid.uuid4(), uuid.uuid4()
    auto_resume.schedule(a, NOW)
    auto_resume.schedule(b, NOW)
    later = NOW + timedelta(minutes=5)

    async def resume(s):
        if s == a:
            raise RuntimeError("login check failed")
        return "gone"  # b is not paused any more

    assert await auto_resume.resume_due(resume, now=later) == []
    assert list(auto_resume.pending()) == [a]
    assert auto_resume.pending()[a] == later + auto_resume.RETRY_AFTER_FAILURE
