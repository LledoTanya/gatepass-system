"""
events.py
---------
A tiny in-process publish/subscribe broker for Server-Sent Events (SSE).

Every browser tab that opens GET /api/events gets its own asyncio.Queue and is
registered as a subscriber. When the backend publishes an event (new request,
status change), it is pushed to every subscriber's queue and streamed out.

This is intentionally simple and in-memory — perfect for a single-process
gatehouse deployment run with `uvicorn`. For multiple workers you would swap
this for Redis pub/sub, but that is out of scope here.
"""

import asyncio
import json
from typing import Any


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def publish(self, event: str, data: dict[str, Any]) -> None:
        """Fan a named event out to every connected client."""
        payload = json.dumps(data)
        message = f"event: {event}\ndata: {payload}\n\n"
        for q in list(self._subscribers):
            await q.put(message)


# Single shared broker for the whole app.
broker = EventBroker()
