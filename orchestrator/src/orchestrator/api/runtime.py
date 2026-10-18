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
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from acp.transport import FileTransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..conversation import OrchestratorConversation
from ..floor import PipelineFloorPolicy, RaiseHandFloorPolicy, RoundRobinFloorPolicy, StopRulesConfig
from ..floor.scorer import HandRaiseScorer
from ..mirror import MessageMirror
from ..build_policy import build_seat_tools
from ..git_guard import save_baseline
from ..workspace import build_branch_name, prepare_branch
from ..models import Agent, Mode, Session, SessionAgent, Task
from ..moderator import ModeratorRunner, Summarizer, Synthesizer
from ..modes_registry import resolve_mode
from ..pubsub import PubSub
from ..sandbox.provider import SandboxProvider, SeatSpec

log = logging.getLogger(__name__)

# Why a session failed to start, kept in memory so the API/UI can show it (cleared on restart).
session_failures: dict[uuid.UUID, str] = {}
# What a starting session is doing right now, so a slow start is visible instead of silent.
session_stages: dict[uuid.UUID, str] = {}

_REPO_ROOT = Path(__file__).resolve().parents[4]


def scrub_failure(text: str) -> str:
    """The failure reason is shown to whoever can reach the API (there is no auth yet), and it can
    contain a script's raw output. Replace absolute host paths -- the repo, the home directory, any
    /Users/<name> or /home/<name> -- so it explains what failed without disclosing the machine's
    layout. The full, unscrubbed error is still in the server log."""
    for real, placeholder in ((str(_REPO_ROOT), "<repo>"), (str(Path.home()), "~")):
        if real and real != "/":
            text = text.replace(real, placeholder)
    return re.sub(r"/(?:Users|home)/[^/\s:]+", "/<user>", text)


RETIRE_GRACE_SECONDS = 180.0


async def _retire_runtime(runtime: "SessionRuntime", grace: float = RETIRE_GRACE_SECONDS) -> None:
    """Stop a session's previous runner (if still running) and its mirror, and wait until both are gone.

    A paused runner winds down after the turn it is in the middle of. Cancelling it mid-turn threw that
    turn away and left a half-delivered assignment for the new runner to trip over, so give it `grace`
    seconds to finish by itself (its mirror must keep running meanwhile, to record the reply) and only
    then cancel what is left."""
    if not runtime.runner_task.done():
        try:
            await asyncio.wait_for(asyncio.shield(runtime.runner_task), timeout=grace)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - a crashed old runner is already handled elsewhere
            pass
    tasks = [runtime.mirror_task]
    if not runtime.runner_task.done():
        tasks.append(runtime.runner_task)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


# Statuses a crashed runner may be moved out of; anything else (finished, cancelled, failed...) is
# already a final answer and must not be overwritten.
_CRASHABLE = ("starting", "active", "stopping", "cancelling")
_failure_tasks: set[asyncio.Task] = set()  # strong refs: the loop only keeps weak ones


_cleanup_tasks: set[asyncio.Task] = set()  # strong refs to mirror-release tasks


async def _stop_mirror_after_run(mirror, mirror_task: asyncio.Task) -> None:
    """Once a runner has ended (finished, stopped, paused, crashed) its mirror has nothing left to
    watch. Stop the poll loop, then do one last poll so replies written just before the end are still
    recorded and published. Without this every finished session leaked a polling task until restart."""
    mirror_task.cancel()
    await asyncio.gather(mirror_task, return_exceptions=True)
    try:
        await mirror.poll_once()
    except Exception:
        log.exception("final mirror poll failed")


def _on_runner_done(task: asyncio.Task, session_id: uuid.UUID, sessionmaker: async_sessionmaker) -> None:
    """A ModeratorRunner that dies with an exception used to vanish: the exception was only logged when
    the task was garbage-collected, and the session stayed `active` forever with no turns. Record it
    on the session instead, so the room and the API show a failed session and why."""
    if task.cancelled() or task.exception() is None:
        return
    exc = task.exception()
    log.error("session %s: moderator runner crashed", session_id, exc_info=exc)
    session_failures[session_id] = scrub_failure(f"while running: {type(exc).__name__}: {exc}")[:600]

    async def mark_failed() -> None:
        async with sessionmaker() as db:
            row = await db.get(Session, session_id)
            if row is not None and row.status in _CRASHABLE:
                row.status = "failed"
                await db.commit()

    t = asyncio.get_running_loop().create_task(mark_failed())
    _failure_tasks.add(t)
    t.add_done_callback(_failure_tasks.discard)


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
            rows = (await db.execute(
                select(SessionAgent, Agent).join(Agent, Agent.id == SessionAgent.agent_id)
                .where(SessionAgent.session_id == session_id, SessionAgent.removed_at.is_(None))
                .order_by(SessionAgent.seat_order)
            )).all()
            workspace = (await db.get(Task, task_id)).workspace
            seats = []
            for sa, a in rows:
                tools, deny = build_seat_tools(a.role or a.name, tuple(a.tools or ()), workspace)
                seats.append(SeatSpec(
                    role=a.role or a.name, name=a.name,
                    brief="\n\n".join(x for x in (a.system_prompt, f"Stance: {a.stance}" if a.stance else "") if x),
                    model=sa.model or a.model, tools=tools, deny=deny))

        mode_config = resolve_mode(mode_row.name)  # re-validated here; the eager POST /sessions
                                                    # check doesn't guarantee the file is unchanged

        session_stages[session_id] = "creating the sandbox (sbx env create)"
        log.info("session %s: %s", session_id, session_stages[session_id])
        session_dir = (root or factory_root()) / str(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        repo_path = None
        guard_file = None
        if mode_config.floor_policy == "pipeline" and workspace and workspace.get("repo_path"):
            # Build mode: put the repository on the session's branch before the sandbox mounts it.
            repo_path = workspace["repo_path"]
            session_stages[session_id] = "creating the build branch"
            await asyncio.to_thread(prepare_branch, repo_path, workspace.get("base_branch") or "main",
                                    build_branch_name(session_id))
            # Baseline of git's own config and hooks, kept outside the repo; a resume keeps the first one.
            guard_file = session_dir / "git-guard.json"
            await asyncio.to_thread(save_baseline, repo_path, guard_file)
            session_stages[session_id] = "creating the sandbox (sbx env create)"
        sandbox = await sandbox_provider.create(
            task_id, name=f"sbx-{task_id}", seats=seats, **({"workspace": repo_path} if repo_path else {}))
        session_stages[session_id] = "starting the agent team in the sandbox"
        log.info("session %s: %s", session_id, session_stages[session_id])
        await sandbox_provider.start_team(sandbox)
        session_stages.pop(session_id, None)

        transport = FileTransport(session_dir)
        pubsub = PubSub()
        conversation = OrchestratorConversation.create(conversation_id=str(session_id),
                                                        factory_dir=session_dir, mode=mode_config)
        mirror = MessageMirror(transport, sessionmaker, pubsub, session_id=session_id)

        if mode_config.floor_policy == "raise-hand":
            policy = RaiseHandFloorPolicy(StopRulesConfig.from_mode(mode_config))
            scorer = scorer_factory()
        elif mode_config.floor_policy == "pipeline":
            policy = PipelineFloorPolicy(mode_config.route,
                                         max_fix_cycles=StopRulesConfig.from_mode(mode_config).max_fix_cycles,
                                         require_evidence=set(StopRulesConfig.from_mode(mode_config).require_evidence))
            scorer = None
        else:
            policy = RoundRobinFloorPolicy()
            scorer = None

        runner = ModeratorRunner(
            session_id=session_id, sessionmaker=sessionmaker, conversation=conversation,
            sandbox_provider=sandbox_provider, sandbox=sandbox, mode=mode_config, policy=policy,
            stop_rules=StopRulesConfig.from_mode(mode_config), pubsub=pubsub,
            summarizer=summarizer, synthesizer=synthesizer, scorer=scorer,
            git_guard_file=guard_file,
        )

        async with sessionmaker() as db:
            row = await db.get(Session, session_id)
            row.status = "active"
            row.started_at = datetime.now(timezone.utc)
            await db.commit()

        # A relaunch (Resume, or restart reconciliation) must retire the previous runtime first. Its
        # mirror polls the same message files and kept running after its runner ended; two mirrors race
        # to record each reply, and only the one that wins publishes it -- so the NEW runner could wait
        # out its whole grant timeout for an answer the old mirror had already swallowed.
        previous = session_runtimes.get(session_id)
        if previous is not None:
            await _retire_runtime(previous)

        mirror_task = asyncio.create_task(mirror.run_forever())
        runner_task = asyncio.create_task(runner.run())
        runner_task.add_done_callback(lambda t: _on_runner_done(t, session_id, sessionmaker))

        def _release_mirror(_t: asyncio.Task) -> None:
            t = asyncio.get_running_loop().create_task(_stop_mirror_after_run(mirror, mirror_task))
            _cleanup_tasks.add(t)
            t.add_done_callback(_cleanup_tasks.discard)

        runner_task.add_done_callback(_release_mirror)
        running_sessions[session_id] = runner_task
        session_runtimes[session_id] = SessionRuntime(conversation=conversation, pubsub=pubsub,
                                                        mirror_task=mirror_task, runner_task=runner_task)
    except Exception as exc:
        log.exception("session %s failed to start", session_id)
        stage = session_stages.pop(session_id, "setup")
        session_failures[session_id] = scrub_failure(f"while {stage}: {type(exc).__name__}: {exc}")[:600]
        async with sessionmaker() as db:
            row = await db.get(Session, session_id)
            if row is not None:
                row.status = "failed"
                await db.commit()
