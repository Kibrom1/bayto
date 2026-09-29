"""Product + acp-mirror schema (M2.2).

Product tables (task, agent, mode, session, session_agent, sandbox, turn, artifact) follow
docs/product-design.md's "Data model (core tables)" section field-for-field.

`message`/`report` mirror exactly what's actually on the wire -- agent-comms/src/acp/models.py's
Envelope, and the dict FileTransport.report()/Conversation.report() persists (there's no Pydantic
model for Report) -- per agent-comms/schema/message.v1.json and report.v1.json. The one column on
each that is NOT a wire field is `session_id`: a FK added so a mirrored row can be related to the
orchestrator session whose factory directory it came from. The M2.3 watcher that will populate
these tables always knows which session it's tailing; nothing else was invented.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------- product tables

class Task(Base):
    __tablename__ = "task"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    brief: Mapped[str | None] = mapped_column(Text)
    output_type: Mapped[str] = mapped_column(String, nullable=False)
    success_criteria: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class Agent(Base):
    __tablename__ = "agent"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str | None] = mapped_column(String)
    system_prompt: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String)
    params_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    tools: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    stance: Mapped[str | None] = mapped_column(String)
    protocol: Mapped[str | None] = mapped_column(String)
    endpoint: Mapped[str | None] = mapped_column(String)
    auth_ref: Mapped[str | None] = mapped_column(String)
    card_json: Mapped[dict | None] = mapped_column(JSONB)
    version: Mapped[str] = mapped_column(String, nullable=False, default="1")


class Mode(Base):
    __tablename__ = "mode"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    phases_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    turn_policy: Mapped[str | None] = mapped_column(String)
    stop_rules_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    output_schema: Mapped[dict | None] = mapped_column(JSONB)


class Session(Base):
    __tablename__ = "session"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("task.id"), nullable=False)
    mode_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mode.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    budget: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SessionAgent(Base):
    """Junction table: (session_id, agent_id) is the natural key, matching product-design's
    session_agent(session_id, agent_id, ...) -- no separate id column was listed."""

    __tablename__ = "session_agent"

    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("session.id"), primary_key=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent.id"), primary_key=True)
    agent_version: Mapped[str | None] = mapped_column(String)
    seat_order: Mapped[int | None] = mapped_column(Integer)
    muted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    harness: Mapped[str | None] = mapped_column(String)
    model: Mapped[str | None] = mapped_column(String)
    isolation: Mapped[str] = mapped_column(String, nullable=False, default="shared")

    __table_args__ = (
        CheckConstraint("isolation in ('shared', 'own')", name="ck_session_agent_isolation"),
    )


class Sandbox(Base):
    """One team sandbox per task (product-design.md), hence the unique task_id."""

    __tablename__ = "sandbox"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("task.id"), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    image: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False)
    cpu: Mapped[int | None] = mapped_column(Integer)
    memory_mb: Mapped[int | None] = mapped_column(Integer)
    egress_policy: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Turn(Base):
    __tablename__ = "turn"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("session.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent.id"), nullable=False)
    round: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tool_calls_json: Mapped[list | None] = mapped_column(JSONB)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost: Mapped[float | None] = mapped_column(Numeric(12, 6))

    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_turn_session_seq"),
    )


class Artifact(Base):
    __tablename__ = "artifact"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("session.id"), nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    content_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    source_turn_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False, default=list)


# ---------------------------------------------------------------- acp mirror tables

class Message(Base):
    """Mirrors acp.models.Envelope / message.v1.json field for field (see module docstring)."""

    __tablename__ = "message"

    message_id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("session.id"))
    protocol: Mapped[str] = mapped_column(String, nullable=False, default="acp/1")
    conversation_id: Mapped[str] = mapped_column(String, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    seq: Mapped[int | None] = mapped_column(Integer)
    from_: Mapped[str] = mapped_column("from", String, nullable=False)
    to: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    visibility: Mapped[str] = mapped_column(String, nullable=False, default="all")
    kind: Mapped[str] = mapped_column(String, nullable=False)
    in_reply_to: Mapped[str | None] = mapped_column(String)
    thread_id: Mapped[str | None] = mapped_column(String)
    requires_ack: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    refs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body_format: Mapped[str] = mapped_column(String, nullable=False, default="markdown")
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("visibility in ('all', 'recipients', 'moderator')", name="ck_message_visibility"),
        UniqueConstraint("conversation_id", "seq", name="uq_message_conversation_seq"),
    )


class Report(Base):
    """Mirrors FileTransport.report()'s dict / report.v1.json field for field (see module
    docstring). Primary key is (message_id, from) -- the same natural key the file transport
    uses for its `{message_id}.{role}.json` report files."""

    __tablename__ = "report"

    message_id: Mapped[str] = mapped_column(String, ForeignKey("message.message_id"), primary_key=True)
    from_: Mapped[str] = mapped_column("from", String, primary_key=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("session.id"))
    status: Mapped[str] = mapped_column(String, nullable=False)
    stage: Mapped[str | None] = mapped_column(String)
    output_ref: Mapped[str | None] = mapped_column(String)
    checks: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
