"""acp/1 envelope and roster models."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from .modes import ModeConfig

PROTOCOL = "acp/1"
BROADCAST = "*"

# Canonical terminal states for Report.status, per docs/agent-communication-protocol.md.
# `status` is an open string (domains may use their own), these are examples, not an
# exhaustive enum.
CANONICAL_STATUSES = (
    "implemented", "review-pass", "review-fail", "blocked-access",
    "needs-human", "failed", "acknowledged",
)


class Kind(str, Enum):
    assignment = "assignment"
    question = "question"
    answer = "answer"
    proposal = "proposal"
    critique = "critique"
    objection = "objection"
    agree = "agree"
    hand_raise = "hand-raise"
    decision_request = "decision-request"
    decision = "decision"
    summary = "summary"
    note = "note"
    resume = "resume"
    report = "report"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Citation(BaseModel):
    """M3.5: optional typed submodel for elements of `Envelope.refs.get('citations', [])`."""
    source: str
    url: str | None = None
    snippet: str | None = None


class Envelope(BaseModel):
    protocol: str = PROTOCOL
    message_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    conversation_id: str
    attempt: int = 1
    seq: int | None = None  # assigned by the transport on send
    from_: str = Field(alias="from")
    to: list[str]
    visibility: Literal["all", "recipients", "moderator"] = "all"
    kind: str
    in_reply_to: str | None = None
    thread_id: str | None = None
    requires_ack: bool = False
    refs: dict[str, Any] = Field(default_factory=dict)
    body: str = ""
    body_format: str = "markdown"
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)

    model_config = {"populate_by_name": True}

    @field_validator("kind")
    @classmethod
    def _kind_ok(cls, v: str) -> str:
        if v in {k.value for k in Kind} or v.startswith("x-"):
            return v
        raise ValueError(f"unknown kind {v!r} (use a known kind or an x-* extension)")

    @field_validator("to", mode="before")
    @classmethod
    def _to_as_list(cls, v: Any) -> Any:
        # A single string is shorthand for one recipient; always stored/serialized as a list.
        return [v] if isinstance(v, str) else v

    @field_validator("to")
    @classmethod
    def _to_nonempty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("'to' must not be empty")
        return v

    def dump(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)


class Roster(BaseModel):
    """Roles present in a conversation and who may send what to whom."""

    # "*" as the (only) entry (M2.11) means an open roster: any role name is a legitimate
    # sender/recipient, for modes seeded from an ad hoc set of agent-template instances
    # rather than a fixed, enumerable team (see acp.modes.ModeConfig.roles).
    roles: list[str]
    # sender -> allowed recipients ("*" = anyone, incl. broadcast)
    send: dict[str, list[str]] = Field(default_factory=dict)

    def can_send(self, sender: str, recipients: list[str]) -> bool:
        open_roster = BROADCAST in self.roles
        if not open_roster and sender not in self.roles and sender != "human":
            return False
        allowed = self.send.get(sender, ["*"])
        if "*" in allowed:
            if open_roster:
                return True
            return all(r == BROADCAST or r in self.roles or r == "human" for r in recipients)
        return all(r in allowed for r in recipients)


class SendPolicy(BaseModel):
    """Which Envelope `kind`s a role may emit -- a different axis than `Roster.can_send`
    (sender -> allowed recipients). A mode file's `roles.<role>.may_send` list."""

    may_send: dict[str, list[str] | None]  # role -> allowed kinds; None/absent role = unrestricted

    def check(self, role: str, kind: str) -> bool:
        # A role not explicitly listed falls back to the mode's "*" entry if it declared
        # one (M2.11's open-roster modes), else is unrestricted -- the pre-M2.11 default.
        allowed = self.may_send[role] if role in self.may_send else self.may_send.get("*")
        return allowed is None or kind in allowed

    @classmethod
    def from_mode(cls, mode: "ModeConfig") -> "SendPolicy":
        return cls(may_send=mode.roles)
