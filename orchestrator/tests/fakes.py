"""Shared test fakes (M2.7/M2.8): no live sbx CLI or Anthropic credentials needed or
assumed available in this dev-team sandbox. Not a test module itself (no test_ prefix).
"""
from __future__ import annotations

import uuid

from orchestrator.sandbox.provider import SandboxInfo, SandboxProvider


class FakeSandboxProvider(SandboxProvider):
    """`create`/`start_team` are real (in-memory) so M2.8's launch_runner can exercise the
    full /start flow; `stop`/`remove`/`reconcile` are unused ABC stubs. `on_wake` simulates
    a participant's eventual response the same way a real Herdr session would: send an
    Envelope through the same FileTransport, then mirror+publish it -- the real
    MessageMirror/PubSub path, not a shortcut."""

    def __init__(self, on_wake=None):
        self.woken: list[str] = []
        self.created_for: list[uuid.UUID] = []
        self.started_teams: list[str] = []
        self._on_wake = on_wake
        self._sandboxes: dict[uuid.UUID, SandboxInfo] = {}

    async def create(self, task_id: uuid.UUID, *, name: str) -> SandboxInfo:
        if task_id in self._sandboxes:
            return self._sandboxes[task_id]
        self.created_for.append(task_id)
        info = SandboxInfo(id=uuid.uuid4(), task_id=task_id, provider="fake", name=name,
                            status="running", image=None, created_at=None, closed_at=None)
        self._sandboxes[task_id] = info
        return info

    async def start_team(self, sandbox: SandboxInfo) -> None:
        self.started_teams.append(sandbox.name)

    async def wake_role(self, sandbox: SandboxInfo, role: str) -> None:
        self.woken.append(role)
        if self._on_wake:
            await self._on_wake(role)

    async def stop(self, sandbox):
        raise NotImplementedError

    async def remove(self, sandbox):
        raise NotImplementedError

    async def reconcile(self):
        raise NotImplementedError


def fake_sandbox() -> SandboxInfo:
    return SandboxInfo(id=uuid.uuid4(), task_id=uuid.uuid4(), provider="fake", name="sbx-test",
                        status="running", image=None, created_at=None, closed_at=None)
