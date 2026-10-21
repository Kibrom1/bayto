"""Pull request for a finished Build session: `GET` shows the draft, `POST` pushes the branch and opens it.

Host-side and on the person's explicit click only. See `pull_request.py` for what is and is not done.
"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .. import audit
from .. import pull_request as pr
from ..git_guard import check_and_restore
from ..models import Artifact, Session, Task
from .deps import get_db_sessionmaker
from .runtime import factory_root
from .types import text

router = APIRouter()
_locks: dict[uuid.UUID, asyncio.Lock] = {}


def _error(code: str, detail: str, field: str | None = None) -> dict:
    body: dict = {"code": code, "detail": detail}
    if field is not None:
        body["field"] = field
    return {"error": body}


class OpenRequest(BaseModel):
    title: text(pr.TITLE_MAX)
    body: text(pr.BODY_MAX)
    draft: bool = False
    # A change set that is not "ready to merge" can still be opened, but only as a draft and only on purpose.
    open_unfinished: bool = False


async def _load(db, session_id: uuid.UUID):
    sess = await db.get(Session, session_id)
    if sess is None:
        raise HTTPException(404, detail=_error("not_found", "session not found"))
    art = (await db.execute(select(Artifact).where(Artifact.session_id == session_id, Artifact.type == "change_set"))
           ).scalars().first()
    if art is None:
        raise HTTPException(409, detail=_error("no_change_set", "this session has no change set to open a pull request for"))
    task = await db.get(Task, sess.task_id)
    return sess, art, task


@router.get("/sessions/{session_id}/pull-request")
async def get_pull_request(session_id: uuid.UUID, sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> dict:
    async with sessionmaker() as db:
        _, art, task = await _load(db, session_id)
    cs = art.content_json
    opened = cs.get("pull_request")
    return {
        "branch": cs["branch"], "base_branch": cs["base_branch"], "ready": cs.get("status") == "ready_to_merge",
        "opened": opened, "draft": pr.draft(cs, task.title if task else None),
    }


@router.post("/sessions/{session_id}/pull-request", status_code=201)
async def open_pull_request(session_id: uuid.UUID, req: OpenRequest,
                            sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker)) -> dict:
    lock = _locks.setdefault(session_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, detail=_error("in_progress", "a pull request is already being opened for this session"))
    async with lock:
        async with sessionmaker() as db:
            sess, art, task = await _load(db, session_id)
            cs = dict(art.content_json)
            ws = (task.workspace or {}) if task else {}
            if cs.get("pull_request"):
                raise HTTPException(409, detail=_error("already_opened", f"already opened: {cs['pull_request']['url']}"))
            if not ws.get("repo_path"):
                raise HTTPException(409, detail=_error("no_workspace", "this task has no repository"))
            ready = cs.get("status") == "ready_to_merge"
            if not ready and not (req.open_unfinished and req.draft):
                raise HTTPException(409, detail=_error(
                    "not_ready", "the team did not finish cleanly; open it as a draft on purpose to proceed"))
            if not cs.get("commits"):
                raise HTTPException(409, detail=_error("no_commits", "there are no commits on the branch"))
            if sess.ended_at is None:
                raise HTTPException(409, detail=_error("still_running", "the session has not ended"))
            branch, base = cs["branch"], cs["base_branch"]
            guard = factory_root() / str(session_id) / "git-guard.json"
            restored = await asyncio.to_thread(check_and_restore, ws["repo_path"], guard)
            if restored:
                audit.emit(action="pr.refused", session_id=session_id, task_id=sess.task_id,
                                   summary="Pull request refused: git settings in the repository had changed",
                                   subject_type="session", subject_id=session_id, detail={"changes": restored})
                await db.commit()
                raise HTTPException(409, detail=_error(
                    "git_guard", "git's own settings in the repository were changed during the session; "
                                 "Bayto restored them, but review the branch before opening a pull request"))
            try:
                opened = await asyncio.to_thread(
                    pr.push_and_open, repo=ws["repo_path"], branch=branch, base=base, title=req.title.strip(),
                    body=req.body, as_draft=req.draft or not ready)
            except pr.PullRequestError as exc:
                audit.emit(action="pr.failed", session_id=session_id, task_id=sess.task_id,
                                   summary=f"Pull request for {branch} failed: {exc.code}",
                                   subject_type="session", subject_id=session_id,
                                   detail={"code": exc.code, "branch": branch, "message": str(exc)[:400]})
                await db.commit()
                status = 502 if exc.code in {"push_failed", "gh_failed", "timeout"} else 409
                raise HTTPException(status, detail=_error(exc.code, str(exc)))
            record = {"url": opened.url, "head_sha": opened.head_sha, "draft": req.draft or not ready,
                      "title": req.title.strip()}
            art.content_json = {**cs, "pull_request": record}
            audit.emit(action="pr.opened", session_id=session_id, task_id=sess.task_id,
                               summary=f"Opened pull request for {branch} into {base}",
                               subject_type="session", subject_id=session_id,
                               detail={"url": opened.url, "branch": branch, "base": base, "head_sha": opened.head_sha,
                                       "draft": record["draft"], "remote": opened.remote_url,
                                       "title": record["title"], "body_sha256": audit.sha256(req.body)})
            await db.commit()
            return record
