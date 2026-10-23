"""Context attached to a task (docs: Context attachments).

A person can attach a file (text, Markdown, code, PDF), a folder on this machine, or a link. The orchestrator
reads it once, stores a text snapshot, and `moderator/runner.py` shows a capped amount of that text to every
seat on every turn as reference material. See context_extract.py for what is guarded and why.

Zero auth, like the rest of the API: anyone who can reach this port can attach a folder path from this machine.
That is the same trust the workspace/preflight endpoint already assumes (a local-only service).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .. import audit
from .. import context_extract as cx
from ..models import Agent, Session, Task, TaskAttachment
from ..retrieval import split_chunks
from .deps import get_db_sessionmaker
from .types import text

router = APIRouter()

MAX_ATTACHMENTS = 10
MAX_ROLES = 12   # roles one attachment can be limited to


def _error(code: str, detail: str, field: str | None = None) -> dict:
    body: dict = {"code": code, "detail": detail}
    if field is not None:
        body["field"] = field
    return {"error": body}


class AttachmentOut(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    source: str | None
    chars: int
    passages: int  # how many parts it is split into when only some are shown to a seat
    truncated: bool
    roles: list[str] | None   # null: everyone can read it
    created_at: datetime


class AttachmentDetail(AttachmentOut):
    content: str


def _out(row: TaskAttachment) -> AttachmentOut:
    return AttachmentOut(id=row.id, kind=row.kind, name=row.name, source=row.source,
                         chars=len(row.content), passages=len(split_chunks(row.content)), truncated=row.truncated,
                         roles=row.roles, created_at=row.created_at)


class FolderIn(BaseModel):
    path: text(1024)
    roles: list[str] | None = None


class UrlIn(BaseModel):
    url: text(2048)
    roles: list[str] | None = None


class RolesIn(BaseModel):
    roles: list[str] | None   # null: everyone; otherwise only these roles


async def _task_or_404(db, task_id: uuid.UUID) -> Task:
    task = await db.get(Task, task_id)
    if task is None:
        raise HTTPException(404, detail=_error("task_not_found", "task not found"))
    return task


async def _room(db, request: Request, task_id: uuid.UUID, session_id: uuid.UUID | None):
    """Context is added and removed only from inside a running session: `session_id` is required, must belong to the
    task, and the session must be running (or paused). Returns the session's runtime, to tell the team."""
    if session_id is None:
        raise HTTPException(422, detail=_error(
            "room_required", "context can only be added from inside a running session", "session_id"))
    sess = await db.get(Session, session_id)
    if sess is None or sess.task_id != task_id:
        raise HTTPException(404, detail=_error("session_not_found", "that session does not belong to this task"))
    runtime = request.app.state.session_runtimes.get(session_id)
    if runtime is None:
        raise HTTPException(409, detail=_error("not_running", "that session is not running"))
    return runtime


async def _require_room(sessionmaker, request: Request, task_id: uuid.UUID, session_id: uuid.UUID | None) -> None:
    """Checked first, before any file is read or any link fetched."""
    async with sessionmaker() as db:
        await _task_or_404(db, task_id)
        await _room(db, request, task_id, session_id)


async def _clean_roles(db, roles: list[str] | None) -> list[str] | None:
    """None (everyone) or a de-duplicated list of roles that exist among the agents. An empty list is refused rather
    than read as "nobody" or "everyone": either guess could surprise the person."""
    if roles is None:
        return None
    clean = list(dict.fromkeys(r.strip() for r in roles if isinstance(r, str)))
    if not clean or "" in clean:
        raise HTTPException(422, detail=_error("roles_invalid", "choose at least one role, or leave it open to everyone", "roles"))
    if len(clean) > MAX_ROLES:
        raise HTTPException(422, detail=_error("roles_invalid", f"an attachment can be limited to up to {MAX_ROLES} roles", "roles"))
    known = set((await db.execute(select(Agent.role).where(Agent.role.is_not(None), Agent.archived_at.is_(None)))).scalars())
    unknown = [r for r in clean if r not in known]
    if unknown:
        raise HTTPException(422, detail=_error("roles_invalid", "no agent has the role: " + ", ".join(u[:64] for u in unknown), "roles"))
    return clean


def _added_note(name: str, kind: str) -> str:
    what = {"file": "a file", "folder": "a folder", "url": "a link"}.get(kind, "a document")
    return (f"I added {what} to the shared context: \"{name}\". It is in the attached context from now on; "
            "read it before your next reply.")


async def _add(sessionmaker, task_id: uuid.UUID, *, kind: str, name: str, source: str | None,
               content: str, truncated: bool, request: Request,
               session_id: uuid.UUID | None = None, roles: list[str] | None = None) -> AttachmentOut:
    async with sessionmaker() as db:
        await _task_or_404(db, task_id)
        runtime = await _room(db, request, task_id, session_id)
        roles = await _clean_roles(db, roles)
        count = await db.scalar(select(func.count()).select_from(TaskAttachment).where(TaskAttachment.task_id == task_id))
        if (count or 0) >= MAX_ATTACHMENTS:
            raise HTTPException(409, detail=_error("too_many_attachments", f"a task can have up to {MAX_ATTACHMENTS} attachments"))
        row = TaskAttachment(task_id=task_id, kind=kind, name=name, source=source, content=content, truncated=truncated, roles=roles)
        db.add(row)
        await db.flush()
        audit.emit(action="attachment.added", summary=f"Attached {kind} {name}" + (" during a session" if session_id else ""),
                           task_id=task_id, session_id=session_id,
                           subject_type="attachment", subject_id=row.id,
                           detail={"kind": kind, "name": name, "source": audit.public_url(source) if kind == "url" else source,
                                   "chars": len(content), "truncated": truncated, "roles": roles, "content_sha256": audit.sha256(content)})
        await db.commit()
        await db.refresh(row)
        out = _out(row)
    if roles is None:  # a restricted one is not announced: the note would name it to seats that may not see it
        await runtime.conversation.send(sender="human", to=["*"], kind="note", body=_added_note(name, kind))
    return out


def _refuse(exc: cx.ContextError, field: str) -> HTTPException:
    return HTTPException(422, detail=_error("attachment_refused", str(exc), field))


@router.get("/tasks/{task_id}/attachments", response_model=list[AttachmentOut])
async def list_attachments(task_id: uuid.UUID, sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> list[AttachmentOut]:
    async with sessionmaker() as db:
        await _task_or_404(db, task_id)
        rows = (await db.execute(select(TaskAttachment).where(TaskAttachment.task_id == task_id)
                                 .order_by(TaskAttachment.created_at))).scalars().all()
    return [_out(r) for r in rows]


@router.get("/tasks/{task_id}/attachments/{attachment_id}", response_model=AttachmentDetail)
async def get_attachment(task_id: uuid.UUID, attachment_id: uuid.UUID,
                         sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> AttachmentDetail:
    async with sessionmaker() as db:
        row = await db.get(TaskAttachment, attachment_id)
        if row is None or row.task_id != task_id:
            raise HTTPException(404, detail=_error("not_found", "attachment not found"))
    return AttachmentDetail(**_out(row).model_dump(), content=row.content)


@router.put("/tasks/{task_id}/attachments/file", status_code=201, response_model=AttachmentOut)
async def attach_file(task_id: uuid.UUID, request: Request, name: str, session_id: uuid.UUID | None = None,
                      roles: list[str] | None = Query(None),
                      sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> AttachmentOut:
    """The file's bytes are the request body (no multipart); `name` is its file name."""
    await _require_room(sessionmaker, request, task_id, session_id)
    async with sessionmaker() as db:   # refuse bad roles before reading the body
        await _clean_roles(db, roles)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cx.MAX_FILE_BYTES:
        raise _refuse(cx.ContextError(f"that file is larger than {cx.MAX_FILE_BYTES // (1024 * 1024)} MB"), "file")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > cx.MAX_FILE_BYTES:
            raise _refuse(cx.ContextError(f"that file is larger than {cx.MAX_FILE_BYTES // (1024 * 1024)} MB"), "file")
    try:
        clean, content, truncated = await asyncio.to_thread(cx.extract_file, name, bytes(data))
    except cx.ContextError as exc:
        raise _refuse(exc, "file")
    return await _add(sessionmaker, task_id, kind="file", name=clean, source=None, content=content, truncated=truncated,
                      request=request, session_id=session_id, roles=roles)


@router.post("/tasks/{task_id}/attachments/folder", status_code=201, response_model=AttachmentOut)
async def attach_folder(task_id: uuid.UUID, req: FolderIn, request: Request, session_id: uuid.UUID | None = None,
                        sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> AttachmentOut:
    await _require_room(sessionmaker, request, task_id, session_id)
    async with sessionmaker() as db:
        await _clean_roles(db, req.roles)
    try:
        resolved, content, truncated = await asyncio.to_thread(cx.extract_folder, req.path)
    except cx.ContextError as exc:
        raise _refuse(exc, "path")
    name = resolved.rstrip("/").rsplit("/", 1)[-1] or resolved
    return await _add(sessionmaker, task_id, kind="folder", name=name, source=resolved, content=content, truncated=truncated,
                      request=request, session_id=session_id, roles=req.roles)


@router.post("/tasks/{task_id}/attachments/url", status_code=201, response_model=AttachmentOut)
async def attach_url(task_id: uuid.UUID, req: UrlIn, request: Request, session_id: uuid.UUID | None = None,
                     sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> AttachmentOut:
    await _require_room(sessionmaker, request, task_id, session_id)
    async with sessionmaker() as db:   # before the link is fetched
        await _clean_roles(db, req.roles)
    try:
        title, content, truncated = await cx.fetch_url(req.url)
    except cx.ContextError as exc:
        raise _refuse(exc, "url")
    return await _add(sessionmaker, task_id, kind="url", name=title, source=req.url, content=content, truncated=truncated,
                      request=request, session_id=session_id, roles=req.roles)


@router.patch("/tasks/{task_id}/attachments/{attachment_id}", response_model=AttachmentOut)
async def set_attachment_roles(task_id: uuid.UUID, attachment_id: uuid.UUID, req: RolesIn, request: Request,
                               session_id: uuid.UUID | None = None,
                               sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> AttachmentOut:
    """Change who can read an attachment (null: everyone). Takes effect on each seat's next turn, because the
    prompt is rebuilt every turn. The team is not sent a note: it would name the attachment to seats that may not see it."""
    async with sessionmaker() as db:
        row = await db.get(TaskAttachment, attachment_id)
        if row is None or row.task_id != task_id:
            raise HTTPException(404, detail=_error("not_found", "attachment not found"))
        await _room(db, request, task_id, session_id)
        before = row.roles
        row.roles = await _clean_roles(db, req.roles)
        if row.roles != before:
            audit.emit(action="attachment.visibility_changed",
                       summary=f"Changed who can read {row.kind} {row.name}: "
                               + ("everyone" if row.roles is None else ", ".join(row.roles)),
                       task_id=task_id, session_id=session_id, subject_type="attachment", subject_id=row.id,
                       detail={"kind": row.kind, "name": row.name, "roles_before": before, "roles_after": row.roles})
        await db.commit()
        await db.refresh(row)
        return _out(row)


@router.delete("/tasks/{task_id}/attachments/{attachment_id}", status_code=204)
async def remove_attachment(task_id: uuid.UUID, attachment_id: uuid.UUID, request: Request,
                            session_id: uuid.UUID | None = None,
                            sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> Response:
    async with sessionmaker() as db:
        row = await db.get(TaskAttachment, attachment_id)
        if row is None or row.task_id != task_id:
            raise HTTPException(404, detail=_error("not_found", "attachment not found"))
        runtime = await _room(db, request, task_id, session_id)
        name, restricted = row.name, row.roles is not None
        await db.delete(row)
        audit.emit(action="attachment.removed", summary=f"Removed {row.kind} {row.name}" + (" during a session" if session_id else ""),
                           task_id=task_id, session_id=session_id,
                           subject_type="attachment", subject_id=row.id, detail={"kind": row.kind, "name": row.name})
        await db.commit()
    if restricted:  # not announced, as when it was added: the note would name it to every seat
        return Response(status_code=204)
    await runtime.conversation.send(
        sender="human", to=["*"], kind="note",
        body=f"I removed \"{name}\" from the shared context. Do not rely on it from now on.")
    return Response(status_code=204)
