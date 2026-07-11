"""In-process pub/sub for run/session events.

SSE/WS event streams wake the instant a new event is written instead of polling.
Durability still goes through the DB; EventHub only removes the polling latency.
notify() is safe to call from any thread.
"""
from __future__ import annotations

import asyncio


class EventHub:
    def __init__(self) -> None:
        self._waiters: dict[int, set[asyncio.Event]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, session_id: int) -> asyncio.Event:
        ev = asyncio.Event()
        self._waiters.setdefault(session_id, set()).add(ev)
        return ev

    def unsubscribe(self, session_id: int, ev: asyncio.Event) -> None:
        bucket = self._waiters.get(session_id)
        if bucket:
            bucket.discard(ev)
            if not bucket:
                self._waiters.pop(session_id, None)

    def _wake(self, session_id: int) -> None:
        for ev in self._waiters.get(session_id, ()):  # set is a snapshot view; ok for set()
            ev.set()

    def notify(self, session_id: int) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._wake(session_id)            # already on the loop thread
        else:
            loop.call_soon_threadsafe(self._wake, session_id)  # from a worker/threadpool thread
