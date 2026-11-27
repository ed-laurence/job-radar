"""Fakes for Clock, Ids and Storage (ARCH-04, TEST-04)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.domain.errors import NotFound

DEFAULT_START = datetime(2026, 11, 25, 1, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, start: datetime = DEFAULT_START) -> None:
        if start.tzinfo is None:
            raise ValueError("FakeClock needs an aware datetime")
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def set(self, moment: datetime) -> None:
        self._now = moment


class SequentialIds:
    """Deterministic ids: 00000000-0000-4000-8000-000000000001, ...002, ..."""

    def __init__(self, start: int = 1) -> None:
        self._next = start

    def new_id(self) -> uuid.UUID:
        value = self._next
        self._next += 1
        return uuid.UUID(f"00000000-0000-4000-8000-{value:012d}")

    def new_correlation_id(self) -> str:
        return str(self.new_id())


class InMemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (data, content_type)

    async def get(self, key: str) -> bytes:
        try:
            return self.objects[key][0]
        except KeyError:
            raise NotFound("Object not found.") from None

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)
