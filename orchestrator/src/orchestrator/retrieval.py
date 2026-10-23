"""Pick the parts of long attachments that matter to one seat.

Attachments are cut to a shared character budget (attachment_prompt.py). Cutting each one at the head means a
seat never sees the clause or function it was asked about if it sits late in a long file. Here the attachment is
split into chunks and ranked against a query (the seat's brief, the task and the latest discussion) with BM25, a
plain keyword score. No embeddings and no database: attachments are capped at 100,000 characters, so ranking at
prompt time is cheap, and nothing needs re-indexing when an attachment changes.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

CHUNK_CHARS = 1500
_WORD = re.compile(r"[a-z0-9_]{3,}")
_STOP = frozenset("""the and for are but not you all can had her was one our out has have this that with from they been
their there would about which when what will your into than then them these those also each more most some such only
over very just should could must may might shall any how why who its his she him did does done being were while""".split())


@dataclass(frozen=True)
class Chunk:
    attachment: int   # index of the attachment it came from
    ordinal: int      # position within that attachment
    text: str
    score: float = 0.0


def tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


def split_chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Cut at paragraph boundaries where possible; a paragraph longer than `size` is cut by length."""
    chunks, current = [], ""
    for para in re.split(r"\n\s*\n", text):
        while len(para) > size:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(para[:size])
            para = para[size:]
        if current and len(current) + len(para) + 2 > size:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{para}" if current else para
    if current.strip():
        chunks.append(current)
    return [c for c in chunks if c.strip()]


def rank(texts: list[str], query: str) -> list[list[Chunk]]:
    """Chunks of each text, scored against the query. Same shape as `texts`; scores are 0 when nothing matches."""
    q = Counter(tokens(query))
    pieces = [[(i, c) for i, c in enumerate(split_chunks(t))] for t in texts]
    docs = [(a, i, c, tokens(c)) for a, group in enumerate(pieces) for i, c in group]
    n = len(docs) or 1
    avg = (sum(len(d[3]) for d in docs) / n) or 1.0
    df = Counter(w for d in docs for w in set(d[3]) if w in q)
    out: list[list[Chunk]] = [[] for _ in texts]
    for a, i, c, toks in docs:
        tf, score = Counter(toks), 0.0
        for w in q:
            if w in tf:
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                score += idf * tf[w] * 2.2 / (tf[w] + 1.2 * (0.25 + 0.75 * len(toks) / avg))
        out[a].append(Chunk(a, i, c, score))
    return out
