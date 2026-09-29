"""Conversation core: validation + permissions on top of a Transport."""
from __future__ import annotations

from .models import Envelope, Roster
from .transport import Transport


class PermissionError_(Exception):
    pass


class Conversation:
    def __init__(self, conversation_id: str, roster: Roster, transport: Transport, attempt: int = 1):
        self.id = conversation_id
        self.roster = roster
        self.transport = transport
        self.attempt = attempt

    def send(self, sender: str, to: list[str], kind: str, body: str = "", **kw) -> Envelope:
        if not self.roster.can_send(sender, to):
            raise PermissionError_(f"{sender!r} may not send to {to}")
        env = Envelope(conversation_id=self.id, attempt=self.attempt, from_=sender, to=to,
                       kind=kind, body=body, **kw)
        return self.transport.send(env)

    def read(self, role: str) -> list[Envelope]:
        return self.transport.read(role, attempt=self.attempt)

    def ack(self, role: str, message_id: str) -> None:
        self.transport.ack(role, message_id)

    def transcript(self) -> list[Envelope]:
        return [e for e in self.transport.all() if e.visibility == "all"]

    def report(self, role: str, message_id: str, status: str, checks=None, summary: str = "",
               stage: str | None = None, output_ref: str | None = None):
        return self.transport.report(role, message_id, status, checks, summary, stage, output_ref)

    def stage(self, new: str | None = None) -> str:
        return self.transport.stage(new)

    def claim(self, name: str) -> bool:
        return self.transport.claim(name)
