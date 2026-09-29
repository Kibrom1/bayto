"""In-process async pub/sub (M2.3). Fans mirrored rows out to subscribers -- e.g. the SSE
HTTP endpoint (M2.8, not built yet) will subscribe a queue per connection. Testable directly
by subscribing an asyncio.Queue and reading events off it.
"""
from __future__ import annotations

import asyncio
from typing import Any


class PubSub:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Any]] = set()

    def subscribe(self) -> asyncio.Queue[Any]:
        q: asyncio.Queue[Any] = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Any]) -> None:
        self._subscribers.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        for q in self._subscribers:
            q.put_nowait(event)
