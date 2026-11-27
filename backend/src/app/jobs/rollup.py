"""Nightly rollup: create upcoming `web_events` partitions, purge events older than 90 days,
and record the board's cards per column. `python -m app.jobs.rollup`."""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from dataclasses import asdict
from datetime import date, timedelta
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.engine import session_factory, system_transaction
from app.adapters.fakes.data import InMemoryWebEvents
from app.compose.devboard import board_stores, job_actor, job_run_log
from app.domain.devboard.schedule import JobName, JobReport, run_job
from app.main import Container, build_container
from app.settings import Settings

RETENTION = timedelta(days=90)
_PURGE = text("DELETE FROM web_events WHERE day < :cutoff RETURNING 1")
_PARTITION = text("SELECT ensure_web_events_partition(:month)")


class WebEventRetention(Protocol):
    async def ensure_partitions(self, today: date) -> None: ...

    async def purge_before(self, cutoff: date) -> int: ...


class PgWebEventRetention:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def ensure_partitions(self, today: date) -> None:
        next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        async with system_transaction(self._sessions) as session:
            for month in (today, next_month):
                await session.execute(_PARTITION, {"month": month})

    async def purge_before(self, cutoff: date) -> int:
        async with system_transaction(self._sessions) as session:
            result = await session.execute(_PURGE, {"cutoff": cutoff})
            return len(result.all())


class InMemoryWebEventRetention:
    def __init__(self, events: InMemoryWebEvents) -> None:
        self._events = events
        self.partitions: list[date] = []

    async def ensure_partitions(self, today: date) -> None:
        self.partitions.append(today)

    async def purge_before(self, cutoff: date) -> int:
        kept = [event for event in self._events.events if event.day >= cutoff]
        purged = len(self._events.events) - len(kept)
        self._events.events[:] = kept
        return purged


def retention_for(container: Container) -> WebEventRetention:
    if container.engine is not None:
        return PgWebEventRetention(session_factory(container.engine))
    events = container.web_events
    assert isinstance(events, InMemoryWebEvents)
    return InMemoryWebEventRetention(events)


async def run(container: Container, *, retention: WebEventRetention | None = None) -> JobReport:
    store = retention or retention_for(container)
    tasks = board_stores(container).tasks

    async def body(report: JobReport) -> None:
        today = container.clock.now().date()
        await store.ensure_partitions(today)
        purged = await store.purge_before(today - RETENTION)
        report.items_seen = purged
        report.note(f"Purged {purged} web events older than 90 days.")
        cards = await tasks.list(job_actor(container).user_id, (), 0, 10_000)
        per_column = Counter(card.column.value for card in cards)
        report.note("Cards per column: " + json.dumps(dict(sorted(per_column.items()))))

    return await run_job(JobName.ROLLUP, job_run_log(container), container.clock, body)


def main() -> int:
    async def go() -> JobReport:
        container = build_container(Settings())
        try:
            return await run(container)
        finally:
            await container.aclose()

    report = asyncio.run(go())
    sys.stdout.write(json.dumps(asdict(report)) + "\n")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
