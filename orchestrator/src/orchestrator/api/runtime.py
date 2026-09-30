"""Session runtime wiring (M2.8): launches a ModeratorRunner -- and the MessageMirror it
depends on to ever see a participant's response -- for one session, tracked in in-process
registries.

Factored into one small reusable function per the architect's M2.9-boundary note:
POST /sessions/{id}/start calls `launch_runner(...)`; a later M2.9 app-startup
reconciliation routine (not built here) can call the SAME function for every
`status == "active"` session found at boot, re-attaching without inlining this logic at
either call site. Two M2.7 decisions already help that resume story: ModeratorRunner
recomputes its ConversationView from Turn/SessionAgent rows rather than hidden state, and
the rolling summary is persisted on Session -- a freshly launched runner after a restart
isn't starting from nothing.

Wiring this needed two pieces the M2.4-M2.7 design left unspecified, both disclosed in
docs/decisions.md: (1) nothing before this task ever actually ran a MessageMirror loop, so
`launch_runner` starts one per session alongside the runner -- without it,
ModeratorRunner._await_response's pubsub wait would never see anything in production, only
in tests that call `mirror.poll_once()` by hand; (2) each session gets its OWN `PubSub`
instance (not one shared across sessions), matching M2.7's `_await_response` docstring
assumption ("one MessageMirror+PubSub pair per session") -- `SessionRuntime` is the
registry the SSE endpoint and the interject endpoint use to find a running session's
`PubSub`/`OrchestratorConversation`.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from acp.transport import FileTransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..conversation import OrchestratorConversation
from ..floor import PersonaBrief, RaiseHandFloorPolicy, RoundRobinFloorPolicy, StopRulesConfig
from ..floor.scorer import HandRaiseScorer
from ..mirror import MessageMirror
from ..models import Agent, Mode, Session, SessionAgent
from ..moderator import ModeratorRunner, Summarizer, Synthesizer
from ..modes_registry import resolve_mode
from ..pubsub import PubSub
from ..sandbox.provider import SandboxProvider

log = logging.getLogger(__name__)

DEFAULT_FACTORY_ROOT = Path(__file__).resolve().parents[2] / ".sessions"


def factory_root() -> Path:
    return Path(os.environ.get("BAYTO_FACTORY_ROOT", DEFAULT_FACTORY_ROOT))


@dataclass
class SessionRuntime:
    """What the REST API needs to reach back into a running session: the interject
    endpoint sends through `conversation`, the SSE endpoint tails `pubsub`."""

    conversation: OrchestratorConversation
    pubsub: PubSub
    mirror_task: asyncio.Task
    runner_task: asyncio.Task


async def _personas(sessionmaker: async_sessionmaker, session_id: uuid.UUID) -> list[PersonaBrief]:
    async with sessionmaker() as db:
        rows = (await db.execute(
            select(Agent).join(SessionAgent, SessionAgent.agent_id == Agent.id)
            .where(SessionAgent.session_id == session_id).order_by(SessionAgent.seat_order)
        )).scalars().all()
    return [PersonaBrief(participant=a.role, role=a.role, stance=a.stance, brief=a.system_prompt or "")
            for a in rows]


async def launch_runner(
    session_id: uuid.UUID,
    *,
    sessionmaker: async_sessionmaker,
    sandbox_provider: SandboxProvider,
    summarizer: Summarizer,
    synthesizer: Synthesizer,
    scorer_factory: Callable[[], HandRaiseScorer],
    running_sessions: dict[uuid.UUID, asyncio.Task],
    session_runtimes: dict[uuid.UUID, SessionRuntime],
    root: Path | None = None,
) -> None:
    """Does the real work of /start: create the sandbox, start the team, build the
    conversation + runner, register both, flip Session.status to "active", then launch the
    mirror and runner loops. Any failure here (including mode re-validation) marks the
    session "failed" rather than leaving it stuck at "starting" forever -- this runs inside
    a background asyncio.Task, so an uncaught exception would otherwise just vanish into
    asyncio's default exception logging with no visible effect on Session.status."""
    try:
        async with sessionmaker() as db:
            session_row = await db.get(Session, session_id)
            mode_row = await db.get(Mode, session_row.mode_id)
            task_id = session_row.task_id

        mode_config = resolve_mode(mode_row.name)  # re-validated here; the eager POST /sessions
                                                    # check doesn't guarantee the file is unchanged
        personas = await _personas(sessionmaker, session_id)

        sandbox = await sandbox_provider.create(task_id, name=f"sbx-{task_id}")
        await sandbox_provider.start_team(sandbox)

        session_dir = (root or factory_root()) / str(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        transport = FileTransport(session_dir)
        pubsub = PubSub()
        conversation = OrchestratorConversation.create(conversation_id=str(session_id),
                                                        factory_dir=session_dir, mode=mode_config)
        mirror = MessageMirror(transport, sessionmaker, pubsub, session_id=session_id)

        if mode_config.floor_policy == "raise-hand":
            policy = RaiseHandFloorPolicy(StopRulesConfig.from_mode(mode_config))
            scorer = scorer_factory()
        else:
            policy = RoundRobinFloorPolicy()
            scorer = None

        runner = ModeratorRunner(
            session_id=session_id, sessionmaker=sessionmaker, conversation=conversation,
            sandbox_provider=sandbox_provider, sandbox=sandbox, mode=mode_config, policy=policy,
            stop_rules=StopRulesConfig.from_mode(mode_config), pubsub=pubsub, personas=personas,
            summarizer=summarizer, synthesizer=synthesizer, scorer=scorer,
        )

        async with sessionmaker() as db:
            row = await db.get(Session, session_id)
            row.status = "active"
            row.started_at = datetime.now(timezone.utc)
            await db.commit()

        mirror_task = asyncio.create_task(mirror.run_forever())
        runner_task = asyncio.create_task(runner.run())
        running_sessions[session_id] = runner_task
        session_runtimes[session_id] = SessionRuntime(conversation=conversation, pubsub=pubsub,
                                                        mirror_task=mirror_task, runner_task=runner_task)
    except Exception:
        log.exception("session %s failed to start", session_id)
        async with sessionmaker() as db:
            row = await db.get(Session, session_id)
            if row is not None:
                row.status = "failed"
                await db.commit()
