"""Mirrors an acp FileTransport's messages/reports into Postgres (M2.3).

Resumes from the last mirrored seq per session (`SELECT MAX(seq) FROM message WHERE
session_id = ...`; message rows already carry seq, mirrored from the Envelope, so no
separate checkpoint table is needed). Idempotent under re-polling via `INSERT ... ON
CONFLICT DO NOTHING`, keyed on the same natural keys the wire format already provides:
message_id for messages, (message_id, from) for reports -- a report is only mirrored once
its message is already present (report.message_id is a real FK to message.message_id).
Newly-mirrored rows are published to a PubSub after commit.

Watch mechanism: polling (see `run_forever`) -- simplest/portable for a team sandbox
factory directory, and keeps this testable without a live filesystem-events setup.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from acp.models import Envelope
from acp.transport import FileTransport

from .models import Message, Report
from .pubsub import PubSub


@dataclass
class MirrorResult:
    new_messages: list[Envelope] = field(default_factory=list)
    new_reports: list[dict] = field(default_factory=list)
    deferred_reports: list[dict] = field(default_factory=list)  # message not mirrored yet


def _message_row(env: Envelope, session_id: uuid.UUID | None) -> dict:
    return {
        "message_id": env.message_id,
        "session_id": session_id,
        "protocol": env.protocol,
        "conversation_id": env.conversation_id,
        "attempt": env.attempt,
        "seq": env.seq,
        "from": env.from_,
        "to": env.to,
        "visibility": env.visibility,
        "kind": env.kind,
        "in_reply_to": env.in_reply_to,
        "thread_id": env.thread_id,
        "requires_ack": env.requires_ack,
        "refs": env.refs,
        "body": env.body,
        "body_format": env.body_format,
        "meta": env.meta,
        "created_at": datetime.fromisoformat(env.created_at),
    }


def _report_row(rec: dict, session_id: uuid.UUID | None) -> dict:
    return {
        "message_id": rec["message_id"],
        "from": rec["from"],
        "session_id": session_id,
        "status": rec["status"],
        "stage": rec["stage"],
        "output_ref": rec["output_ref"],
        "checks": rec["checks"],
        "summary": rec["summary"],
        "verified": rec["verified"],
    }


class MessageMirror:
    """One instance per session's factory directory (transport)."""

    def __init__(self, transport: FileTransport, sessionmaker: async_sessionmaker,
                 pubsub: PubSub | None = None, session_id: uuid.UUID | None = None):
        self.transport = transport
        self.sessionmaker = sessionmaker
        self.pubsub = pubsub
        self.session_id = session_id

    async def poll_once(self) -> MirrorResult:
        result = MirrorResult()
        async with self.sessionmaker() as db:
            last_seq = await db.scalar(
                select(func.coalesce(func.max(Message.seq), 0)).where(Message.session_id == self.session_id)
            )
            envelopes = await asyncio.to_thread(self.transport.messages_after, last_seq)

            inserted_message_ids: set[str] = set()
            if envelopes:
                rows = [_message_row(e, self.session_id) for e in envelopes]
                stmt = (
                    pg_insert(Message)
                    .values(rows)
                    .on_conflict_do_nothing(index_elements=["message_id"])
                    .returning(Message.message_id)
                )
                inserted_message_ids = {r[0] for r in (await db.execute(stmt)).all()}
            result.new_messages = [e for e in envelopes if e.message_id in inserted_message_ids]

            report_dicts = await asyncio.to_thread(self.transport.reports)
            if report_dicts:
                candidate_ids = {r["message_id"] for r in report_dicts}
                known = set((await db.execute(
                    select(Message.message_id).where(Message.message_id.in_(candidate_ids))
                )).scalars())
                mirrorable = [r for r in report_dicts if r["message_id"] in known]
                result.deferred_reports = [r for r in report_dicts if r["message_id"] not in known]

                inserted_report_keys: set[tuple[str, str]] = set()
                if mirrorable:
                    rows = [_report_row(r, self.session_id) for r in mirrorable]
                    stmt = (
                        pg_insert(Report)
                        .values(rows)
                        .on_conflict_do_nothing(index_elements=["message_id", "from"])
                        .returning(Report.message_id, Report.from_)
                    )
                    inserted_report_keys = {(r[0], r[1]) for r in (await db.execute(stmt)).all()}
                result.new_reports = [r for r in mirrorable if (r["message_id"], r["from"]) in inserted_report_keys]

            await db.commit()

        if self.pubsub:
            for env in result.new_messages:
                self.pubsub.publish({"type": "message", "data": env.dump()})
            for rec in result.new_reports:
                self.pubsub.publish({"type": "report", "data": rec})
        return result

    async def run_forever(self, interval: float = 1.0, stop_event: asyncio.Event | None = None) -> None:
        """Poll in a loop until `stop_event` is set. Not exercised by tests directly (no
        live filesystem-events setup needed); `poll_once` is the unit under test."""
        stop_event = stop_event or asyncio.Event()
        while not stop_event.is_set():
            await self.poll_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
