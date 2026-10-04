"""SandboxProvider: the seam between the orchestrator and a team sandbox runtime (M2.4).

An ABC on purpose, not a Protocol: docs/product-design.md calls this out as a real seam
for a later cloud provider, and an ABC fails fast with TypeError if an implementation
misses a method, which documents the contract better than structural typing for
something meant to be extended.

SandboxInfo is a plain frozen dataclass (not the SQLAlchemy `Sandbox` row) so callers and
tests never hold a session-attached object; it mirrors `models.Sandbox`'s columns 1:1.
Didn't reach for pydantic v2 here since these values come from our own DB, not external
input needing validation.
"""
from __future__ import annotations

import abc
import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class SandboxInfo:
    id: uuid.UUID
    task_id: uuid.UUID
    provider: str
    name: str
    status: str  # "creating" | "running" | "stopped" | "removed" | "error"
    image: str | None
    created_at: datetime
    closed_at: datetime | None


class SandboxProviderError(RuntimeError):
    """Base error for SandboxProvider implementations."""


class SandboxCommandError(SandboxProviderError):
    """A provider's underlying CLI/subprocess call exited non-zero."""

    def __init__(self, argv: list[str], returncode: int, stderr: str) -> None:
        self.argv = argv
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"command {argv!r} exited {returncode}: {stderr}")


@dataclass(frozen=True, slots=True)
class SandboxDrift:
    task_id: uuid.UUID | None  # None if this is an orphan sbx ls has no DB row for
    name: str
    db_status: str | None  # None if there was no DB row at all
    live_status: str | None  # None if the DB row exists but nothing is live
    resolution: str  # "synced" | "marked_removed" | "orphan_reported"


class SandboxProvider(abc.ABC):
    """Lifecycle of one team sandbox per task. Five small methods matching the five verbs
    the work-plan item lists, so the orchestrator's task/session service composes them and
    each stays independently unit-testable (same philosophy as mirror.py's
    poll_once/run_forever split -- no combined "provision" method, and reconcile() is a
    startup-time action, not a poll loop)."""

    @abc.abstractmethod
    async def create(self, task_id: uuid.UUID, *, name: str) -> SandboxInfo:
        """Idempotent: if a Sandbox row already exists for task_id, return it (re-issuing
        the create command only if its last known status is not running/creating).
        Sandbox.task_id is unique, so this is the only entry point that inserts a row."""

    @abc.abstractmethod
    async def start_team(self, sandbox: SandboxInfo) -> None:
        """Start one session per roles/team.tsv participant inside the sandbox."""

    async def restart_team(self, sandbox: SandboxInfo) -> None:
        """Bring the team back after stop() + a restart of the sandbox (M1.14).

        A stop keeps files, the factory dir and Claude transcripts but kills every herdr and
        claude process, and leaves the factory's `team-started` marker behind, so a plain
        start_team() would print 'Team already started' and do nothing. Not abstract: the
        default is start_team(), correct for a sandbox that has never run a team."""
        await self.start_team(sandbox)

    @abc.abstractmethod
    async def wake_role(self, sandbox: SandboxInfo, role: str, fallback: bool = False) -> None:
        """Notify a single role it has a new assignment. If fallback=True, use a local model. """

    @abc.abstractmethod
    async def stop(self, sandbox: SandboxInfo) -> SandboxInfo:
        """Stop the sandbox's processes; no-op-safe if already stopped."""

    @abc.abstractmethod
    async def remove(self, sandbox: SandboxInfo) -> SandboxInfo:
        """Tear down permanently: status=removed, closed_at=now. Transcripts are not
        touched -- they already live in the factory dir + Postgres per
        product-design.md's lifecycle step 4."""

    @abc.abstractmethod
    async def reconcile(self) -> list[SandboxDrift]:
        """Call once at orchestrator startup. Cross-check DB Sandbox rows against what the
        provider reports live and fix drift. Not a polling loop."""
