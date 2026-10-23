"""The opt-in semantic stage of attachment retrieval (docs/per-agent-context-rag.md, P4).

`semantic_scores` returns, for the attachments one seat may read, the cosine similarity of every chunk to the seat's
query, shaped like `retrieval.rank`'s result so `attachment_prompt(..., semantic=...)` can mix it with BM25.

What leaves the machine: the text of the chunks of the attachments passed in, and the query (the seat's brief, the
task and the discussion summary), sent to the embedding provider (providers/embeddings.py). The caller passes only
attachments the seat may read (`visible_to`), so an attachment hidden from a seat is never embedded on its behalf.
Vectors are cached in `attachment_chunk_vec`, so each chunk is sent once per embedding model; only the query is sent
on later turns.

It never fails a turn. Any problem (no key, provider down, a bad answer, a database error) returns None, the caller
ranks by keyword alone, and the first failure after a success (or at start) is written to the log once.
"""
from __future__ import annotations

import hashlib
import logging
import math

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .models import AttachmentChunkVec
from .providers import embedding_client
from .providers.base import ProviderError
from .providers.embeddings import EmbeddingClient
from .retrieval import split_chunks

log = logging.getLogger(__name__)
QUERY_CHARS = 4000   # the query is cut to this before it is sent
_noted = False        # a failure has been logged and no success has followed


def _note(reason: str) -> None:
    global _noted
    if not _noted:
        _noted = True
        log.warning("semantic retrieval unavailable, ranking by keyword only: %s", reason)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cosine(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0 or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


async def semantic_scores(sessionmaker, items, query: str, *, client: EmbeddingClient | None = None) -> list[list[float]] | None:
    """`items` must already be filtered to what the seat may read (rows with `.id` and `.content`)."""
    global _noted
    try:
        client = client or embedding_client()
        scores = await _scores(sessionmaker, client, list(items), query[:QUERY_CHARS])
    except ProviderError as exc:
        _note(str(exc))   # ProviderError messages never hold a key
        return None
    except Exception as exc:  # noqa: BLE001 - nothing here may fail a turn; the type only, never the message
        _note(f"{type(exc).__name__}")
        return None
    _noted = False
    return scores


async def _scores(sessionmaker, client: EmbeddingClient, items: list, query: str) -> list[list[float]]:
    chunks = [split_chunks(a.content) for a in items]
    ids = [a.id for a in items]
    async with sessionmaker() as db:
        cached = {(r.attachment_id, r.ordinal): r for r in (await db.execute(
            select(AttachmentChunkVec).where(AttachmentChunkVec.attachment_id.in_(ids)))).scalars()} if ids else {}
    vecs: dict[tuple, list[float]] = {}
    missing: list[tuple] = []   # (attachment id, ordinal, hash, text)
    for aid, group in zip(ids, chunks):
        for ordinal, text in enumerate(group):
            h, row = _hash(text), cached.get((aid, ordinal))
            if row is not None and row.content_hash == h and row.model == client.tag:
                vecs[(aid, ordinal)] = row.vec
            else:
                missing.append((aid, ordinal, h, text))
    # One request for the query and any chunk not cached yet (the query goes last).
    out = await client.embed([m[3] for m in missing] + [query or " "])
    query_vec = out[-1]
    for (aid, ordinal, h, _), vec in zip(missing, out):
        vecs[(aid, ordinal)] = vec
    count = {aid: len(group) for aid, group in zip(ids, chunks)}
    stale = [k for k in cached if k[1] >= count[k[0]]]   # chunks the attachment no longer has
    if missing or stale:
        try:   # a failed cache write costs a re-embed next time, never the turn
            async with sessionmaker() as db:
                for aid, ordinal, h, _ in missing:
                    vec = vecs[(aid, ordinal)]
                    stmt = pg_insert(AttachmentChunkVec).values(attachment_id=aid, ordinal=ordinal, content_hash=h,
                                                                model=client.tag, vec=vec)
                    await db.execute(stmt.on_conflict_do_update(
                        index_elements=["attachment_id", "ordinal"],
                        set_={"content_hash": h, "model": client.tag, "vec": vec}))
                for aid, ordinal in stale:
                    await db.execute(delete(AttachmentChunkVec).where(
                        AttachmentChunkVec.attachment_id == aid, AttachmentChunkVec.ordinal == ordinal))
                await db.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("could not cache attachment vectors (%s)", type(exc).__name__)
    return [[cosine(query_vec, vecs[(aid, o)]) for o in range(len(group))] for aid, group in zip(ids, chunks)]
