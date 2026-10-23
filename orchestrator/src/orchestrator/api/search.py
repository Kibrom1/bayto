"""GET /search: full-text search across the transcripts and syntheses of past sessions.

Postgres full-text with `websearch_to_tsquery`, so what a person types is never query syntax: quotes, `<`, `:`,
`&`, `|` are plain input (a stray operator simply matches nothing). Only messages with visibility "all" are searched,
the same rule recall uses, so a private note is never found; archived sessions are skipped unless
`include_archived=true`. Message bodies hold no attachment text, so attachments are not searched.

The WHERE clauses repeat the expressions of the GIN indexes made by migration b8c9d0e1f2a7
(`to_tsvector('english', body)` / `to_tsvector('english', content_json)`).

Snippets are plain text: the match is wrapped in `[[` and `]]`, never HTML. A `[[` or `]]` already in the text is
spaced apart first so a marker always means a match.

Zero auth, like the rest of the API.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..recall import RECALL_KINDS, synthesis_text
from .deps import get_db_sessionmaker

router = APIRouter()

MIN_QUERY_CHARS = 2
MAX_QUERY_CHARS = 200
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
_HEADLINE = 'StartSel="[[", StopSel="]]", MaxWords=30, MinWords=12, ShortWord=2, MaxFragments=2, FragmentDelimiter=" ... "'


class SearchHit(BaseModel):
    task_id: uuid.UUID
    task_title: str
    session_id: uuid.UUID
    message_id: str | None   # None for a synthesis
    speaker: str             # seat name, "human", or "synthesis"
    kind: str                # "answer" | "assignment" | "synthesis"
    snippet: str
    created_at: datetime | None
    rank: float


class SearchOut(BaseModel):
    query: str
    results: list[SearchHit]


def _unbracket(s: str) -> str:
    return s.replace("[[", "[ [").replace("]]", "] ]")


_MESSAGES = text("""
select m.message_id, m.session_id, m."from" as speaker, m.kind, m.created_at, t.id as task_id, t.title,
       ts_rank_cd(to_tsvector('english', m.body), q.q) as rank,
       ts_headline('english', replace(replace(m.body, '[[', '[ ['), ']]', '] ]'), q.q, :opts) as snippet
from message m
join session s on s.id = m.session_id
join task t on t.id = s.task_id,
     websearch_to_tsquery('english', :q) as q(q)
where to_tsvector('english', m.body) @@ q.q
  and m.visibility = 'all' and m.kind = any(:kinds)
  and (:include_archived or s.archived_at is null)
  and (cast(:task_id as uuid) is null or t.id = cast(:task_id as uuid))
order by rank desc, m.created_at desc
limit :limit
""")

_SYNTHESES = text("""
select a.id, a.session_id, a.content_json, s.started_at, t.id as task_id, t.title,
       ts_rank_cd(to_tsvector('english', a.content_json), q.q) as rank
from artifact a
join session s on s.id = a.session_id
join task t on t.id = s.task_id,
     websearch_to_tsquery('english', :q) as q(q)
where to_tsvector('english', a.content_json) @@ q.q
  and a.type = 'synthesis'
  and (:include_archived or s.archived_at is null)
  and (cast(:task_id as uuid) is null or t.id = cast(:task_id as uuid))
order by rank desc, s.started_at desc nulls last
limit :limit
""")

_HEADLINE_OF = text("select ts_headline('english', :body, websearch_to_tsquery('english', :q), :opts)")


@router.get("/search", response_model=SearchOut)
async def search(
    q: str = Query(..., max_length=MAX_QUERY_CHARS),
    task_id: uuid.UUID | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1),
    include_archived: bool = False,
    sessionmaker: async_sessionmaker = Depends(get_db_sessionmaker),
) -> SearchOut:
    q = q.strip()
    if len(q) < MIN_QUERY_CHARS:
        raise HTTPException(422, detail={"error": {"code": "query_too_short", "field": "q",
                                                   "detail": f"Type at least {MIN_QUERY_CHARS} characters to search."}})
    limit = min(limit, MAX_LIMIT)
    args = {"q": q, "task_id": str(task_id) if task_id else None, "include_archived": include_archived,
            "limit": limit}
    hits: list[SearchHit] = []
    async with sessionmaker() as db:
        for r in (await db.execute(_MESSAGES, {**args, "kinds": list(RECALL_KINDS), "opts": _HEADLINE})).mappings():
            hits.append(SearchHit(task_id=r["task_id"], task_title=r["title"], session_id=r["session_id"],
                                  message_id=r["message_id"], speaker=r["speaker"], kind=r["kind"],
                                  snippet=r["snippet"], created_at=r["created_at"], rank=float(r["rank"])))
        for r in (await db.execute(_SYNTHESES, args)).mappings():
            body = _unbracket(synthesis_text(r["content_json"]))
            snippet = await db.scalar(_HEADLINE_OF, {"body": body, "q": q, "opts": _HEADLINE})
            hits.append(SearchHit(task_id=r["task_id"], task_title=r["title"], session_id=r["session_id"],
                                  message_id=None, speaker="synthesis", kind="synthesis", snippet=snippet or "",
                                  created_at=r["started_at"], rank=float(r["rank"])))
    hits.sort(key=lambda h: (h.rank, h.created_at.timestamp() if h.created_at else 0.0), reverse=True)
    return SearchOut(query=q, results=hits[:limit])
