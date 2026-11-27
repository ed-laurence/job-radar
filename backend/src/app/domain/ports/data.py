"""Repository and notification ports used by foundation services."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


@dataclass(frozen=True)
class Notification:
    """A Postgres NOTIFY. Payloads carry ids only; content is re-read under RLS."""

    channel: str
    payload: str


class NotificationHub(Protocol):
    """One shared LISTEN connection per process, fanned out in memory (ARCH-31)."""

    def subscribe(
        self, channel: str
    ) -> AbstractAsyncContextManager[AsyncIterator[Notification]]: ...


@dataclass(frozen=True)
class StoredWebEvent:
    at: datetime
    day: date
    session_id: str
    role_tag: str
    type: str
    route: str
    name: str | None = None
    metric: str | None = None
    value: float | None = None
    rating: str | None = None


@dataclass(frozen=True)
class PageViews:
    route: str
    views: int
    sessions: int


@dataclass(frozen=True)
class VitalSample:
    route: str
    metric: str
    p75: float
    samples: int


class WebEventsRepository(Protocol):
    async def insert_many(self, events: Sequence[StoredWebEvent]) -> int: ...

    async def page_views(self, start: date, end: date) -> list[PageViews]: ...

    async def product_event_sessions(self, start: date, end: date) -> list[tuple[str, str]]:
        """Distinct `(session_id, event name)` pairs in `[start, end)`."""
        ...

    async def vitals_p75(self, start: date, end: date) -> list[VitalSample]: ...
