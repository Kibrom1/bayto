"""acp/1 envelope and roster models."""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

PROTOCOL = "acp/1"
BROADCAST = "*"


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


def _now() -> float:
    return time.time()


class Envelope(BaseModel):
    protocol: str = PROTOCOL
    message_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    conversation_id: str
    attempt: int = 1
    seq: int | None = None  # assigned by the transport on send
    from_: str = Field(alias="from")
    to: list[str]
    visibility: str = "room"  # room | private
    kind: str
    in_reply_to: str | None = None
    thread_id: str | None = None
    requires_ack: bool = False
    refs: list[str] = Field(default_factory=list)
    body: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=_now)

    model_config = {"populate_by_name": True}

    @field_validator("kind")
    @classmethod
    def _kind_ok(cls, v: str) -> str:
        if v in {k.value for k in Kind} or v.startswith("x-"):
            return v
        raise ValueError(f"unknown kind {v!r} (use a known kind or an x-* extension)")

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

    roles: list[str]
    # sender -> allowed recipients ("*" = anyone, incl. broadcast)
    send: dict[str, list[str]] = Field(default_factory=dict)

    def can_send(self, sender: str, recipients: list[str]) -> bool:
        if sender not in self.roles and sender != "human":
            return False
        allowed = self.send.get(sender, ["*"])
        if "*" in allowed:
            return all(r == BROADCAST or r in self.roles or r == "human" for r in recipients)
        return all(r in allowed for r in recipients)
