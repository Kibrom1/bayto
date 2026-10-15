"""In-process async pub/sub (M2.3). Fans mirrored rows out to subscribers -- the SSE HTTP
endpoint subscribes a queue per connection. Testable directly by subscribing an asyncio.Queue
and reading events off it.

Queues are bounded. A subscriber that stops draining (a stalled browser tab, a dead proxy) used
to grow without limit; now, when its queue fills, its backlog is dropped and a single
`OVERFLOW` event is left in its place. The SSE handler ends the stream on that event, and the
client reconnects with its last seen message id and is replayed from the database, so nothing
is lost -- the slow consumer just pays with a reconnect instead of the server paying with memory.
"""
from __future__ import annotations

import asyncio
from typing import Any

QUEUE_MAX = 1000
OVERFLOW: dict[str, Any] = {"type": "overflow"}


class PubSub:
    def __init__(self, queue_max: int = QUEUE_MAX) -> None:
        self._subscribers: set[asyncio.Queue[Any]] = set()
        self._queue_max = queue_max

    def subscribe(self, *, bounded: bool = True) -> asyncio.Queue[Any]:
        """`bounded=False` is for in-process consumers that must never lose an event (the
        moderator waiting on a participant's reply); network-facing consumers stay bounded."""
        q: asyncio.Queue[Any] = asyncio.Queue(maxsize=self._queue_max if bounded else 0)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Any]) -> None:
        self._subscribers.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop this subscriber's backlog and leave one marker so its handler ends the stream.
                while not q.empty():
                    q.get_nowait()
                q.put_nowait(OVERFLOW)
                self._subscribers.discard(q)
