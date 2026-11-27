"""One LISTEN connection per process, fanned out to in-memory subscribers (ARCH-31).

Payloads carry ids only. Subscribers re-read content under their own RLS scope.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from app.adapters.db.engine import plain_url
from app.domain.ports import Notification

_QUEUE_SIZE = 256

type Connection = asyncpg.Connection


class PgNotificationHub:
    def __init__(self, database_url: str, channels: tuple[str, ...] = ("run_steps",)) -> None:
        self._url = plain_url(database_url)
        self._channels = channels
        self._connection: Connection | None = None
        self._subscribers: dict[str, set[asyncio.Queue[Notification]]] = defaultdict(set)

    async def start(self) -> None:
        connection: Connection = await asyncpg.connect(self._url)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        for channel in self._channels:
            await connection.add_listener(channel, self._on_notify)
        self._connection = connection

    async def stop(self) -> None:
        if self._connection is not None:
            with contextlib.suppress(Exception):
                await self._connection.close()
            self._connection = None

    @asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncGenerator[AsyncIterator[Notification]]:
        if channel not in self._channels:
            raise ValueError("unknown channel")
        queue: asyncio.Queue[Notification] = asyncio.Queue(maxsize=_QUEUE_SIZE)
        self._subscribers[channel].add(queue)
        try:
            yield _drain(queue)
        finally:
            self._subscribers[channel].discard(queue)

    def _on_notify(self, _conn: object, _pid: int, channel: str, payload: str) -> None:
        note = Notification(channel=channel, payload=payload)
        for queue in list(self._subscribers.get(channel, ())):
            # A slow subscriber drops notifications rather than stalling everyone; streams
            # re-read from their last seq, so a gap is recoverable.
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(note)


async def _drain(queue: asyncio.Queue[Notification]) -> AsyncGenerator[Notification]:
    while True:
        yield await queue.get()
