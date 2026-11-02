"""Opt-in: resume a session that paused because Claude's usage limit was reached, once the limit should have reset.

Off unless BAYTO_AUTO_RESUME_ON_LIMIT=1. Only sessions that the runner paused for a usage limit are registered here;
a session waiting for a person's answer is never touched. Each attempt is one ordinary resume: if the limit is still
on, the runner pauses the session again with a new reset time, so there is no tight loop. The schedule is held in
memory, so it is lost if Bayto restarts (the session then stays paused until someone presses Resume).
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

log = logging.getLogger("orchestrator.auto_resume")

ENV = "BAYTO_AUTO_RESUME_ON_LIMIT"
BUFFER = timedelta(seconds=60)       # after the stated reset time, so the first call does not land a second early
RETRY_AFTER_FAILURE = timedelta(minutes=5)
CHECK_EVERY_SECONDS = 60

_due: dict[uuid.UUID, datetime] = {}


def enabled() -> bool:
    return os.environ.get(ENV, "").strip() == "1"


def schedule(session_id: uuid.UUID, retry_at: datetime) -> None:
    """Remember to resume `session_id` just after `retry_at` (UTC). A no-op unless the feature is on."""
    if enabled():
        _due[session_id] = retry_at + BUFFER


def forget(session_id: uuid.UUID) -> None:
    _due.pop(session_id, None)


def pending() -> dict[uuid.UUID, datetime]:
    return dict(_due)


async def resume_due(resume: Callable[[uuid.UUID], Awaitable[str]], now: datetime | None = None) -> list[uuid.UUID]:
    """Resume every session whose time has come. `resume` returns "ok", "gone" (nothing to do: not paused any more) or
    "retry" (could not start now, for example the login check failed); anything it raises counts as "retry"."""
    now = now or datetime.now(timezone.utc)
    resumed: list[uuid.UUID] = []
    for session_id, when in list(_due.items()):
        if when > now:
            continue
        try:
            outcome = await resume(session_id)
        except Exception:  # noqa: BLE001 - one bad session must not stop the others
            log.exception("auto-resume of session %s failed", session_id)
            outcome = "retry"
        if outcome == "retry":
            _due[session_id] = now + RETRY_AFTER_FAILURE
            continue
        _due.pop(session_id, None)
        if outcome == "ok":
            resumed.append(session_id)
    return resumed


async def loop(resume: Callable[[uuid.UUID], Awaitable[str]]) -> None:
    while True:
        await asyncio.sleep(CHECK_EVERY_SECONDS)
        try:
            for sid in await resume_due(resume):
                log.info("auto-resumed session %s after the Claude usage limit", sid)
        except Exception:  # noqa: BLE001
            log.exception("auto-resume check failed")
