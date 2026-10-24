"""GET /sessions/{id}/export: a session as a Markdown decision record.

Only what everyone in the room could read goes in: messages with visibility "all". Free text (task brief, answers,
synthesis values) is never interpreted as Markdown: transcript text and the brief sit in code fences longer than any
run of backticks inside them, synthesis values in block quotes, and every one-line label (title, names) has its line
breaks collapsed, so nothing a participant wrote can open a heading or close a fence of the record. Attachment text is
never included, only names, how much a seat read and who may read each one.
"""
from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models import Agent, Artifact, Message, Mode, Session, SessionAgent, Task, TaskAttachment
from ..moderator.budget import session_usage
from .deps import get_db_sessionmaker

router = APIRouter()

INTERNAL_KINDS = {"assignment", "hand-raise", "resume", "report"}   # protocol traffic, as the room hides it
DECISION_TYPES = ("decision", "decisions", "action_items", "action_item")
HUMAN = "human"


def _one_line(value: object, limit: int = 200) -> str:
    s = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    s = s.replace("|", "\\|").replace("`", "'")
    return s[:limit] if s else "-"


def _fence(text: str, info: str = "") -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{info}\n{text}\n{ticks}"


def _quote(text: str) -> str:
    return "\n".join("> " + line if line else ">" for line in text.splitlines() or [""])


def _render(value: object, depth: int = 0) -> str:
    """Free-form artifact content as quoted text: keys become bold one-line labels, values stay verbatim in quotes."""
    pad = "  " * depth
    if value is None or value == "":
        return ""
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            inner = _render(v, depth + 1)
            if inner:
                parts.append(f"{pad}**{_one_line(k, 80)}**\n\n{inner}")
        return "\n\n".join(parts)
    if isinstance(value, list):
        return "\n\n".join(x for x in (_render(v, depth + 1) for v in value) if x)
    return _quote(str(value))


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50].strip("-")
    return s or "session"


def _ts(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC") if dt else "-"


def _section(title: str, body: str) -> str:
    return f"## {title}\n\n{body}\n"


async def build_record(sessionmaker: async_sessionmaker, session_id: uuid.UUID) -> tuple[str, str] | None:
    """(file name, Markdown) or None when the session does not exist."""
    async with sessionmaker() as db:
        sess = await db.get(Session, session_id)
        if sess is None:
            return None
        task = await db.get(Task, sess.task_id)
        mode = await db.get(Mode, sess.mode_id)
        seats = (await db.execute(
            select(SessionAgent, Agent).join(Agent, Agent.id == SessionAgent.agent_id)
            .where(SessionAgent.session_id == session_id)
            .order_by(SessionAgent.seat_order.nulls_last(), Agent.name))).all()
        artifacts = (await db.execute(select(Artifact).where(Artifact.session_id == session_id)
                                      .order_by(Artifact.id))).scalars().all()
        messages = (await db.execute(
            select(Message).where(Message.session_id == session_id, Message.visibility == "all")
            .order_by(Message.seq.nulls_last(), Message.created_at))).scalars().all()
        attachments = (await db.execute(select(TaskAttachment).where(TaskAttachment.task_id == sess.task_id)
                                        .order_by(TaskAttachment.created_at))).scalars().all()
    usage = await session_usage(sessionmaker, session_id)

    title = task.title if task else "Untitled task"
    finished = sess.status in {"finished", "completed", "stopped", "ended"}
    out: list[str] = [f"# Decision record: {_one_line(title)}\n"]
    if not finished:
        out.append(f"> **Unfinished session.** Status is `{_one_line(sess.status, 40)}`; this record shows the discussion so far, "
                   "and there may be no synthesis yet.\n")

    out.append(_section("Question", ("**Task:** " + _one_line(title)) +
                        ("\n\n" + _fence(task.brief) if task and task.brief else "\n\n_No brief was written._") +
                        (("\n\n**Success criteria:**\n\n" + _fence(task.success_criteria)) if task and task.success_criteria else "")))

    stop = sess.stop_reason
    out.append(_section("Session", "\n".join([
        f"- Mode: {_one_line(mode.name if mode else '-')}",
        f"- Started: {_ts(sess.started_at)}",
        f"- Ended: {_ts(sess.ended_at)}",
        f"- Status: {_one_line(sess.status, 40)}",
        f"- Stop reason: {_one_line(stop) if stop else 'none recorded'}",
        f"- Session id: {sess.id}",
    ])))

    rows = ["| Name | Role | Model | Notes |", "| --- | --- | --- | --- |"]
    for sa, agent in seats:
        notes = ", ".join(n for n in ("removed during the session" if sa.removed_at else "", "muted" if sa.muted else "") if n)
        rows.append(f"| {_one_line(agent.name)} | {_one_line(agent.role)} | {_one_line(sa.model or agent.model)} | {notes or '-'} |")
    out.append(_section("Roster", "\n".join(rows)))

    by_type: dict[str, list[Artifact]] = {}
    for a in artifacts:
        by_type.setdefault(a.type, []).append(a)

    def artifact_body(kind: str | tuple[str, ...], empty: str) -> str:
        kinds = (kind,) if isinstance(kind, str) else kind
        found = [a for k in kinds for a in by_type.get(k, [])]
        text = "\n\n".join(r for r in (_render(a.content_json) for a in found) if r)
        return text or empty

    out.append(_section("Synthesis", artifact_body("synthesis", "_No synthesis was written." + ("" if finished else " The session has not finished.") + "_")))
    out.append(_section("Minority report", artifact_body("minority_report", "_No dissent was recorded._")))
    if any(by_type.get(k) for k in DECISION_TYPES):
        out.append(_section("Decisions and action items", artifact_body(DECISION_TYPES, "")))

    visible = [m for m in messages if m.kind not in INTERNAL_KINDS and not m.kind.startswith("x-")]

    # What was read: names and the per-turn mode from meta, never text. Role visibility comes from the attachment itself.
    read: dict[str, dict] = {}
    for m in visible:
        for item in (m.meta or {}).get("attachments") or []:
            name = item.get("name") if isinstance(item, dict) else None
            if name:
                read.setdefault(name, {"seats": set()})["seats"].add(m.from_)
    if read:
        roles_of = {a.name: a.roles for a in attachments}
        lines = ["| Attachment | Who may read it | Read by |", "| --- | --- | --- |"]
        for name, info in sorted(read.items()):
            roles = roles_of.get(name, "gone")
            who = "removed since" if roles == "gone" else ("everyone" if roles is None else "only " + ", ".join(map(_one_line, roles)))
            lines.append(f"| {_one_line(name)} | {who} | {_one_line(', '.join(sorted(info['seats'])))} |")
        out.append(_section("Attachments read", "\n".join(lines)))
    else:
        out.append(_section("Attachments read", "_No attachments were read in this session._"))

    out.append(_section("Cost and tokens", "\n".join([
        f"- Tokens in: {usage.tokens_in}", f"- Tokens out: {usage.tokens_out}",
        f"- Total tokens: {usage.tokens_in + usage.tokens_out}", f"- Cost: ${usage.cost:.4f}"])))

    if visible:
        parts = []
        for m in visible:
            who = _one_line(m.from_, 80)
            tag = " (human interjection)" if m.from_ == HUMAN else ""
            to = "" if m.to in (["*"], []) else f" to {_one_line(', '.join(m.to))}"
            parts.append(f"### {who}{tag}{to} - {_ts(m.created_at)}\n\n{_fence(m.body or '')}")
        body = "\n\n".join(parts)
    else:
        body = "_No messages yet._"
    out.append(_section("Transcript", "Messages that were not visible to everyone are left out.\n\n" + body))

    return f"bayto-{_slug(title)}-{str(sess.id)[:8]}.md", "\n".join(out)


@router.get("/sessions/{session_id}/export")
async def export_session(
    session_id: uuid.UUID,
    format: str = "md",
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
) -> Response:
    if format != "md":
        raise HTTPException(422, detail={"error": {"code": "unsupported_format", "detail": "only format=md is supported", "field": "format"}})
    built = await build_record(sessionmaker, session_id)
    if built is None:
        raise HTTPException(404, detail={"error": {"code": "not_found", "detail": f"session {session_id} not found", "field": "id"}})
    filename, md = built
    return Response(md, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
