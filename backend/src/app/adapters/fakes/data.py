"""In-memory fakes for repositories, the notification hub and the LLM usage store."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import date

from app.domain.ports import (
    LlmCallRecord,
    LlmFeature,
    Notification,
    PageViews,
    StoredWebEvent,
    VitalSample,
)


class InMemoryWebEvents:
    def __init__(self) -> None:
        self.events: list[StoredWebEvent] = []

    async def insert_many(self, events: Sequence[StoredWebEvent]) -> int:
        self.events.extend(events)
        return len(events)

    async def page_views(self, start: date, end: date) -> list[PageViews]:
        views: dict[str, int] = defaultdict(int)
        sessions: dict[str, set[str]] = defaultdict(set)
        for event in self._between(start, end):
            if event.type == "page_view":
                views[event.route] += 1
                sessions[event.route].add(event.session_id)
        return [PageViews(route, count, len(sessions[route])) for route, count in views.items()]

    async def product_event_sessions(self, start: date, end: date) -> list[tuple[str, str]]:
        pairs = {
            (event.session_id, event.name)
            for event in self._between(start, end)
            if event.type == "product" and event.name is not None
        }
        return sorted(pairs)

    async def vitals_p75(self, start: date, end: date) -> list[VitalSample]:
        values: dict[tuple[str, str], list[float]] = defaultdict(list)
        for event in self._between(start, end):
            if event.type == "web_vital" and event.metric and event.value is not None:
                values[(event.route, event.metric)].append(event.value)
        return [
            VitalSample(
                route=route, metric=metric, p75=percentile(samples, 0.75), samples=len(samples)
            )
            for (route, metric), samples in sorted(values.items())
        ]

    def _between(self, start: date, end: date) -> list[StoredWebEvent]:
        return [event for event in self.events if start <= event.day < end]


def percentile(samples: Sequence[float], fraction: float) -> float:
    """Linear interpolation, matching Postgres `percentile_cont`."""
    ordered = sorted(samples)
    if not ordered:
        return 0.0
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


class InMemoryNotificationHub:
    """Fan-out without Postgres. `publish` stands in for a NOTIFY."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[Notification]]] = defaultdict(set)

    @asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncGenerator[AsyncIterator[Notification]]:
        queue: asyncio.Queue[Notification] = asyncio.Queue(maxsize=256)
        self._subscribers[channel].add(queue)
        try:
            yield _drain(queue)
        finally:
            self._subscribers[channel].discard(queue)

    async def publish(self, channel: str, payload: str) -> None:
        for queue in list(self._subscribers.get(channel, ())):
            queue.put_nowait(Notification(channel=channel, payload=payload))

    def subscriber_count(self, channel: str) -> int:
        return len(self._subscribers.get(channel, ()))


async def _drain(queue: asyncio.Queue[Notification]) -> AsyncGenerator[Notification]:
    while True:
        yield await queue.get()


class InMemoryUsageStore:
    def __init__(self) -> None:
        self.calls: list[LlmCallRecord] = []
        self.spend: dict[tuple[date, LlmFeature], float] = defaultdict(float)

    async def record_call(self, record: LlmCallRecord) -> None:
        self.calls.append(record)

    async def spent(self, day: date, feature: LlmFeature) -> float:
        return self.spend[(day, feature)]

    async def add_spend(self, day: date, feature: LlmFeature, cost_usd: float) -> None:
        self.spend[(day, feature)] += cost_usd
