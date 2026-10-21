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

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .. import context_extract as cx
from ..models import Session, Task, TaskAttachment
from .deps import get_db_sessionmaker
from .types import text

router = APIRouter()

MAX_ATTACHMENTS = 10


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
    truncated: bool
    created_at: datetime


class AttachmentDetail(AttachmentOut):
    content: str


def _out(row: TaskAttachment) -> AttachmentOut:
    return AttachmentOut(id=row.id, kind=row.kind, name=row.name, source=row.source,
                         chars=len(row.content), truncated=row.truncated, created_at=row.created_at)


class FolderIn(BaseModel):
    path: text(1024)


class UrlIn(BaseModel):
    url: text(2048)


async def _task_or_404(db, task_id: uuid.UUID) -> Task:
    task = await db.get(Task, task_id)
    if task is None:
        raise HTTPException(404, detail=_error("task_not_found", "task not found"))
    return task


async def _check_session(db, task_id: uuid.UUID, session_id: uuid.UUID | None) -> None:
    """`session_id` says the person is adding this from inside a room; it must be a session of this task."""
    if session_id is None:
        return
    sess = await db.get(Session, session_id)
    if sess is None or sess.task_id != task_id:
        raise HTTPException(404, detail=_error("session_not_found", "that session does not belong to this task"))


async def _tell_room(request: Request, session_id: uuid.UUID | None, body: str) -> None:
    """Put a line from the human in the room's transcript (and so in each agent's next prompt) when the session is
    running. Not running: nothing to tell; the next turn reads the attachments anyway."""
    if session_id is None:
        return
    runtime = request.app.state.session_runtimes.get(session_id)
    if runtime is not None:
        await runtime.conversation.send(sender="human", to=["*"], kind="note", body=body)


def _added_note(name: str, kind: str) -> str:
    what = {"file": "a file", "folder": "a folder", "url": "a link"}.get(kind, "a document")
    return (f"I added {what} to the shared context: \"{name}\". It is in the attached context from now on; "
            "read it before your next reply.")


async def _add(sessionmaker, task_id: uuid.UUID, *, kind: str, name: str, source: str | None,
               content: str, truncated: bool, request: Request | None = None,
               session_id: uuid.UUID | None = None) -> AttachmentOut:
    async with sessionmaker() as db:
        await _task_or_404(db, task_id)
        await _check_session(db, task_id, session_id)
        count = await db.scalar(select(func.count()).select_from(TaskAttachment).where(TaskAttachment.task_id == task_id))
        if (count or 0) >= MAX_ATTACHMENTS:
            raise HTTPException(409, detail=_error("too_many_attachments", f"a task can have up to {MAX_ATTACHMENTS} attachments"))
        row = TaskAttachment(task_id=task_id, kind=kind, name=name, source=source, content=content, truncated=truncated)
        db.add(row)
        await db.commit()
        await db.refresh(row)
        out = _out(row)
    if request is not None:
        await _tell_room(request, session_id, _added_note(name, kind))
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
                      sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> AttachmentOut:
    """The file's bytes are the request body (no multipart); `name` is its file name."""
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
                      request=request, session_id=session_id)


@router.post("/tasks/{task_id}/attachments/folder", status_code=201, response_model=AttachmentOut)
async def attach_folder(task_id: uuid.UUID, req: FolderIn, request: Request, session_id: uuid.UUID | None = None,
                        sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> AttachmentOut:
    try:
        resolved, content, truncated = await asyncio.to_thread(cx.extract_folder, req.path)
    except cx.ContextError as exc:
        raise _refuse(exc, "path")
    name = resolved.rstrip("/").rsplit("/", 1)[-1] or resolved
    return await _add(sessionmaker, task_id, kind="folder", name=name, source=resolved, content=content, truncated=truncated,
                      request=request, session_id=session_id)


@router.post("/tasks/{task_id}/attachments/url", status_code=201, response_model=AttachmentOut)
async def attach_url(task_id: uuid.UUID, req: UrlIn, request: Request, session_id: uuid.UUID | None = None,
                     sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> AttachmentOut:
    try:
        title, content, truncated = await cx.fetch_url(req.url)
    except cx.ContextError as exc:
        raise _refuse(exc, "url")
    return await _add(sessionmaker, task_id, kind="url", name=title, source=req.url, content=content, truncated=truncated,
                      request=request, session_id=session_id)


@router.delete("/tasks/{task_id}/attachments/{attachment_id}", status_code=204)
async def remove_attachment(task_id: uuid.UUID, attachment_id: uuid.UUID, request: Request,
                            session_id: uuid.UUID | None = None,
                            sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> Response:
    async with sessionmaker() as db:
        row = await db.get(TaskAttachment, attachment_id)
        if row is None or row.task_id != task_id:
            raise HTTPException(404, detail=_error("not_found", "attachment not found"))
        await _check_session(db, task_id, session_id)
        name = row.name
        await db.delete(row)
        await db.commit()
    await _tell_room(request, session_id,
                     f"I removed \"{name}\" from the shared context. Do not rely on it from now on.")
    return Response(status_code=204)
