"""reconcile_on_startup (M2.9): cross-cutting glue over sandbox/session state, called once
from app.py's FastAPI lifespan before this process starts serving requests. Same
top-level-placement reasoning as OrchestratorConversation/MessageMirror -- it composes
SandboxProvider and Session rows, doesn't belong inside either.

Sequencing matters: SandboxProvider.reconcile() runs FIRST, before touching any Session
row -- launch_runner() needs a trustworthy Sandbox row to wake participants against;
relaunching before reconciling risks calling wake_role() against a sandbox reconcile()
would have flagged as gone.

Drift is expected/tolerable, never fatal to startup: if SandboxProvider.reconcile() itself
raises (e.g. no `sbx` CLI on this host at all -- the same M2.4-flagged gap, now also hit at
startup), this logs and continues with an empty drift set RATHER THAN treating every
resumable session as orphaned. Assuming "no drift info" means "every sandbox is gone" would
be far more destructive than the actual failure warrants -- a transient reconcile() hiccup
must never mass-orphan a fleet of otherwise-healthy sessions. Orphan detection only ever
runs when reconcile() actually succeeded.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import Session
from .moderator.runner import STATUS_ACTIVE, STATUS_CANCELLING, STATUS_STOPPING
from .sandbox.provider import SandboxProvider

log = logging.getLogger(__name__)

# Product-owner-confirmed: a session whose sandbox is gone gets a status DISTINCT from
# both "active" (would be a correctness bug for anything that polls session state) and the
# generic failure status a session gets from an explicit run failure (would foreclose a
# future recovery flow -- M3's task board could specifically query for this status and
# offer "recreate sandbox and resume" later).
STATUS_ORPHANED = "orphaned"

# Sessions that must already have a running sandbox (launch_runner only ever sets one of
# these AFTER SandboxProvider.create()/start_team() succeeded) -- orphan-checked against
# drift before relaunching.
_ORPHAN_CHECK_STATUSES = (STATUS_ACTIVE, STATUS_STOPPING, STATUS_CANCELLING)

# STATUS_STARTING is resumable too (the process may have died between /start setting
# "starting" and launch_runner() reaching "active"), but is deliberately NOT orphan-checked:
# a session that never got as far as creating a sandbox is not "orphaned", it just needs an
# ordinary (idempotent, per M2.4) relaunch attempt, which will create one if needed.
_STATUS_STARTING = "starting"
RESUMABLE_STATUSES = (_STATUS_STARTING, *_ORPHAN_CHECK_STATUSES)

# STATUS_NEEDS_HUMAN is deliberately excluded: it's a genuine pause (M2.7 does not build a
# resume-from-human-decision path), so there is no runner loop to relaunch for it yet --
# relaunching would just re-evaluate and immediately re-pause. "created"/"finished"/
# "failed"/STATUS_ORPHANED are terminal-or-not-yet-started and also excluded.


@dataclass
class ReconcileReport:
    relaunched: list[uuid.UUID] = field(default_factory=list)
    skipped_orphaned: list[uuid.UUID] = field(default_factory=list)
    failed_to_launch: list[tuple[uuid.UUID, str]] = field(default_factory=list)


async def reconcile_on_startup(
    sandbox_provider: SandboxProvider,
    sessionmaker: async_sessionmaker,
    launch_runner: Callable[[uuid.UUID], Awaitable[None]],
) -> ReconcileReport:
    """`launch_runner` here is a `session_id -> None` closure (api.runtime.launch_runner's
    full signature, partially applied by the caller -- app.py's lifespan) so this module
    doesn't need to know about SandboxProvider factories, LLM seams or the in-process
    registries; it only orchestrates *which* sessions get relaunched.

    Per-session failure isolation: each relaunch attempt is wrapped in its own try/except
    so one session's problem (missing sandbox, bad mode file, whatever) never aborts
    reconciliation for every other session.
    """
    report = ReconcileReport()

    try:
        drift = await sandbox_provider.reconcile()
        reconcile_ok = True
    except Exception:
        log.exception("SandboxProvider.reconcile() failed at startup -- continuing without "
                      "drift info; no session will be orphaned based on this alone")
        drift = []
        reconcile_ok = False

    for d in drift:
        log.info("sandbox drift at startup: name=%s resolution=%s db_status=%s live_status=%s",
                  d.name, d.resolution, d.db_status, d.live_status)
    live_by_task_id = {d.task_id: d for d in drift if d.task_id is not None}

    async with sessionmaker() as db:
        rows = (await db.execute(
            select(Session).where(Session.status.in_(RESUMABLE_STATUSES))
        )).scalars().all()
        sessions = [(row.id, row.task_id, row.status) for row in rows]

    for session_id, task_id, status in sessions:
        if reconcile_ok and status in _ORPHAN_CHECK_STATUSES:
            drift_entry = live_by_task_id.get(task_id)
            if drift_entry is None or drift_entry.resolution == "marked_removed":
                await _mark_orphaned(sessionmaker, session_id)
                report.skipped_orphaned.append(session_id)
                log.warning("session %s orphaned at startup: no live sandbox for task %s", session_id, task_id)
                continue

        try:
            await launch_runner(session_id)
            report.relaunched.append(session_id)
        except Exception as exc:
            report.failed_to_launch.append((session_id, str(exc)))
            log.exception("failed to relaunch session %s at startup", session_id)

    return report


async def _mark_orphaned(sessionmaker: async_sessionmaker, session_id: uuid.UUID) -> None:
    async with sessionmaker() as db:
        row = await db.get(Session, session_id)
        row.status = STATUS_ORPHANED
        await db.commit()
