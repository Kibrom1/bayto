"""The audit trail: an append-only record of who did what, and what each agent was allowed to do.

`record()` adds a row in the caller's own transaction, so a change and its record are committed together or not at
all. Rows are chained: each stores the hash of the one before it and its own hash over its content, so editing a row,
or deleting one from the middle, breaks the chain and `verify()` says where. The table also refuses UPDATE, DELETE and
TRUNCATE (see the migration). Whoever can drop that trigger can still rewrite the whole chain, so for stronger
assurance keep the head hash that `verify()` returns somewhere else.

What is NOT recorded: attachment contents, agent instructions and message text. The record names things and gives
sizes and fingerprints; the content lives where the person already sees it.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditEvent

GENESIS = "0" * 64
_LOCK_KEY = 7_243_001  # serialises writers so the chain has one order


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(*, at: datetime, actor: str, action: str, summary: str, task_id, session_id,
               subject_type, subject_id, detail: dict, prev_hash: str) -> str:
    return json.dumps({
        "at": at.astimezone(timezone.utc).isoformat(), "actor": actor, "action": action, "summary": summary,
        "task_id": str(task_id) if task_id else None, "session_id": str(session_id) if session_id else None,
        "subject_type": subject_type, "subject_id": subject_id, "detail": detail, "prev_hash": prev_hash,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def event_hash(row: AuditEvent) -> str:
    return sha256(_canonical(at=row.at, actor=row.actor, action=row.action, summary=row.summary,
                             task_id=row.task_id, session_id=row.session_id, subject_type=row.subject_type,
                             subject_id=row.subject_id, detail=row.detail, prev_hash=row.prev_hash))


def public_url(url: str | None) -> str | None:
    """A link without its query string or fragment: those are where tokens and signed parameters live."""
    return url.split("#", 1)[0].split("?", 1)[0] if url else url


async def record(db: AsyncSession, *, action: str, summary: str, actor: str = "person",
                 task_id: uuid.UUID | None = None, session_id: uuid.UUID | None = None,
                 subject_type: str | None = None, subject_id: Any = None, detail: dict | None = None) -> AuditEvent:
    """Add one event to the log inside `db`'s current transaction. The caller commits."""
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
    prev = await db.scalar(select(AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1)) or GENESIS
    row = AuditEvent(
        at=datetime.now(timezone.utc), actor=actor, action=action, summary=summary[:500], task_id=task_id,
        session_id=session_id, subject_type=subject_type, subject_id=str(subject_id) if subject_id is not None else None,
        detail=json.loads(json.dumps(detail or {}, default=str)), prev_hash=prev, hash="",
    )
    row.hash = event_hash(row)
    db.add(row)
    await db.flush()
    return row


async def verify(db: AsyncSession) -> dict:
    """Walk the whole chain. `ok` is False at the first row whose link or content does not match."""
    prev, count = GENESIS, 0
    rows = await db.stream_scalars(select(AuditEvent).order_by(AuditEvent.seq))
    async for row in rows:
        if row.prev_hash != prev or event_hash(row) != row.hash:
            return {"ok": False, "checked": count, "first_bad_seq": row.seq, "head_hash": prev}
        prev, count = row.hash, count + 1
    return {"ok": True, "checked": count, "first_bad_seq": None, "head_hash": prev}
