"""OrchestratorConversation: async wrapper around agent-comms' Conversation (M2.5).

Reuses agent-comms as-is via asyncio.to_thread rather than a second, parallel async-native
engine. The protocol doc's fully-async Transport/PostgresTransport is explicitly "later,"
not built, no migration exists for it; a second implementation of can_send/visibility/
stage logic would risk drift on what "may this role send this" means. The orchestrator
process runs on the host and the factory directory is host-mounted, so FileTransport isn't
sandbox-only -- it's just file I/O against a path the orchestrator can already see. See
docs/decisions.md, 2026-09-29.

This also answers where orchestrator-authored messages (assignments, pipeline routing)
come from: the orchestrator calls `send()` on its own OrchestratorConversation, like any
other participant. Those envelopes land in the same files MessageMirror already polls, so
they're mirrored into Postgres for free -- no separate write path to keep in sync.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Self

from acp.core import Conversation
from acp.models import Envelope, SendPolicy
from acp.modes import ModeConfig
from acp.transport import FileTransport


class OrchestratorConversation:
    def __init__(self, conversation: Conversation) -> None:
        self._conversation = conversation

    @classmethod
    def create(cls, *, conversation_id: str, factory_dir: Path, mode: ModeConfig, attempt: int = 1) -> Self:
        transport = FileTransport(factory_dir)
        roster = mode.send_roster()
        policy = SendPolicy.from_mode(mode)
        conv = Conversation(conversation_id, roster, transport, attempt=attempt,
                             send_policy=policy, moderator_role=mode.moderator)
        return cls(conv)

    async def send(self, sender: str, to: list[str], kind: str, body: str = "", **kw) -> Envelope:
        return await asyncio.to_thread(self._conversation.send, sender, to, kind, body, **kw)

    async def transcript_for(self, viewer: str) -> list[Envelope]:
        return await asyncio.to_thread(self._conversation.transcript, viewer)

    async def stage(self, new: str | None = None) -> str:
        return await asyncio.to_thread(self._conversation.stage, new)

    async def claim(self, name: str) -> bool:
        return await asyncio.to_thread(self._conversation.claim, name)
