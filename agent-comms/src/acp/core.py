"""Conversation core: validation + permissions on top of a Transport."""
from __future__ import annotations

from .models import Envelope, Roster, SendPolicy
from .transport import Transport


class PermissionError_(Exception):
    pass


def is_visible_to(visibility: str, from_: str, to: list[str], viewer: str, *,
                   moderator_role: str | None) -> bool:
    """Pure predicate behind `Conversation.transcript()`; also importable by orchestrator
    code reading the mirrored `message` table directly (same visibility/from_/to fields,
    mirrored 1:1)."""
    if visibility == "all":
        return True
    if visibility == "recipients":
        return viewer == from_ or viewer in to
    if visibility == "moderator":
        return viewer == from_ or viewer == moderator_role
    raise ValueError(f"unknown visibility {visibility!r}")


class Conversation:
    def __init__(self, conversation_id: str, roster: Roster, transport: Transport, attempt: int = 1,
                 send_policy: SendPolicy | None = None, moderator_role: str | None = None):
        self.id = conversation_id
        self.roster = roster
        self.transport = transport
        self.attempt = attempt
        self.send_policy = send_policy
        self.moderator_role = moderator_role

    def send(self, sender: str, to: list[str], kind: str, body: str = "", **kw) -> Envelope:
        if not self.roster.can_send(sender, to):
            raise PermissionError_(f"{sender!r} may not send to {to}")
        if self.send_policy and not self.send_policy.check(sender, kind):
            raise PermissionError_(f"{sender!r} may not send kind {kind!r}")
        env = Envelope(conversation_id=self.id, attempt=self.attempt, from_=sender, to=to,
                       kind=kind, body=body, **kw)
        return self.transport.send(env)

    def read(self, role: str) -> list[Envelope]:
        return self.transport.read(role, attempt=self.attempt)

    def ack(self, role: str, message_id: str) -> None:
        self.transport.ack(role, message_id)

    def transcript(self, viewer: str) -> list[Envelope]:
        """Viewer-scoped history: a `recipients`- or `moderator`-visibility message is
        hidden from anyone but its intended reader (M1.1b's deferred enforcement). There is
        no "see everything" default -- that silently leaked recipients/moderator messages
        to every caller, which is the bug this fixes."""
        return [e for e in self.transport.all()
                if is_visible_to(e.visibility, e.from_, e.to, viewer, moderator_role=self.moderator_role)]

    def report(self, role: str, message_id: str, status: str, checks=None, summary: str = "",
               stage: str | None = None, output_ref: str | None = None):
        return self.transport.report(role, message_id, status, checks, summary, stage, output_ref)

    def stage(self, new: str | None = None) -> str:
        return self.transport.stage(new)

    def claim(self, name: str) -> bool:
        return self.transport.claim(name)
