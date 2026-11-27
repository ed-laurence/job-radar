"""Nightly digest: summarise the last day of dev-board activity on the open-model route
(`digest` in `ai/models.yaml`: Model Garden MaaS, falling back to Claude), within the job's
daily spend cap. Without room in the cap, or offline without a recording, a plain template
digest is stored instead. `python -m app.jobs.digest`."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.engine import session_factory, system_transaction, user_transaction
from app.adapters.fakes.devboard import InMemoryRunStore, InMemoryTaskStore
from app.adapters.llm.providers.replay import ReplayFixtureMissing
from app.compose.devboard import board_stores, devboard_llm, job_actor, job_run_log
from app.domain.devboard.model import EventKind, RunStatus
from app.domain.devboard.schedule import JOB_CAPS, Budget, JobName, JobReport, run_job
from app.domain.ports import LLM, DataBlock, LlmAttribution, LlmFeature, LlmRequest
from app.main import Container, build_container
from app.settings import Settings

WINDOW = timedelta(days=1)
STATE_KEY = "digest:latest"
SYSTEM = "You write a short, factual nightly digest of a Kanban board's activity for its owner."
INSTRUCTIONS = (
    "Summarise the activity counts in the data block in one headline and at most five short "
    "bullets. Mention only numbers that are in the data."
)

_EVENTS = text(
    "SELECT kind, count(*) FROM task_events WHERE at >= :since GROUP BY kind ORDER BY kind"
)
_RUNS = text(
    "SELECT status, count(*) FROM runs WHERE finished_at >= :since GROUP BY status ORDER BY status"
)
_COLUMNS = text(
    'SELECT "column", count(*) FROM dev_tasks WHERE deleted_at IS NULL GROUP BY 1 ORDER BY 1'
)
_SAVE = text(
    "INSERT INTO app_state (key, value) VALUES (:key, CAST(:value AS jsonb))"
    " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
)


class DigestOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: str = Field(min_length=1, max_length=200)
    bullets: list[str] = Field(min_length=1, max_length=5)


@dataclass(frozen=True)
class Activity:
    events: dict[str, int] = field(default_factory=dict[str, int])
    runs: dict[str, int] = field(default_factory=dict[str, int])
    columns: dict[str, int] = field(default_factory=dict[str, int])

    def as_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


class BoardActivity(Protocol):
    async def since(self, user_id: uuid.UUID, since: datetime) -> Activity: ...


class DigestStore(Protocol):
    async def save(self, key: str, value: dict[str, object]) -> None: ...


class PgBoardActivity:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def since(self, user_id: uuid.UUID, since: datetime) -> Activity:
        async with user_transaction(self._sessions, user_id) as session:
            events = {str(k): int(n) for k, n in await session.execute(_EVENTS, {"since": since})}
            runs = {str(k): int(n) for k, n in await session.execute(_RUNS, {"since": since})}
            columns = {str(k): int(n) for k, n in await session.execute(_COLUMNS)}
        return Activity(events=events, runs=runs, columns=columns)


class InMemoryBoardActivity:
    def __init__(self, tasks: InMemoryTaskStore, runs: InMemoryRunStore) -> None:
        self._tasks = tasks
        self._runs = runs

    async def since(self, user_id: uuid.UUID, since: datetime) -> Activity:
        events = Counter(e.kind.value for e in self._tasks.events_log if e.at >= since)
        finished = Counter(
            run.status.value
            for run in self._runs.runs.values()
            if run.finished_at is not None and run.finished_at >= since
        )
        cards = await self._tasks.list(user_id, (), 0, 10_000)
        columns = Counter(card.column.value for card in cards)
        return Activity(
            dict(sorted(events.items())),
            dict(sorted(finished.items())),
            dict(sorted(columns.items())),
        )


class PgDigestStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def save(self, key: str, value: dict[str, object]) -> None:
        async with system_transaction(self._sessions) as session:
            await session.execute(_SAVE, {"key": key, "value": json.dumps(value)})


class InMemoryDigestStore:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, object]] = {}

    async def save(self, key: str, value: dict[str, object]) -> None:
        self.values[key] = value


def template_digest(activity: Activity) -> DigestOutput:
    moved = activity.events.get(EventKind.MOVED.value, 0)
    created = activity.events.get(EventKind.CREATED.value, 0)
    succeeded = activity.runs.get(RunStatus.SUCCEEDED.value, 0)
    failed = activity.runs.get(RunStatus.FAILED.value, 0)
    return DigestOutput(
        headline=f"{created} new cards and {moved} moves in the last day.",
        bullets=[f"Agent runs: {succeeded} succeeded, {failed} failed."],
    )


def _sources(container: Container) -> tuple[BoardActivity, DigestStore]:
    if container.engine is not None:
        sessions = session_factory(container.engine)
        return PgBoardActivity(sessions), PgDigestStore(sessions)
    stores = board_stores(container)
    assert isinstance(stores.tasks, InMemoryTaskStore)
    assert isinstance(stores.runs, InMemoryRunStore)
    return InMemoryBoardActivity(stores.tasks, stores.runs), InMemoryDigestStore()


async def run(
    container: Container,
    *,
    activity: BoardActivity | None = None,
    store: DigestStore | None = None,
    llm: LLM | None = None,
) -> JobReport:
    default_activity, default_store = _sources(container)
    source, sink = activity or default_activity, store or default_store
    model = llm or devboard_llm(container)
    budget = Budget(JOB_CAPS[JobName.DIGEST], container.usage, container.clock)
    actor = job_actor(container)

    async def body(report: JobReport) -> None:
        counts = await source.since(actor.user_id, container.clock.now() - WINDOW)
        report.items_seen = sum(counts.events.values())
        digest, provider = template_digest(counts), "template"
        if not await budget.has_room():
            report.note("Daily spend cap reached: stored the template digest.")
        else:
            request = LlmRequest(
                feature=LlmFeature.DIGEST,
                system=SYSTEM,
                instructions=INSTRUCTIONS,
                attribution=LlmAttribution(user_id=actor.user_id),
                data=(DataBlock("activity", counts.as_json()),),
            )
            try:
                result = await model.generate(request, DigestOutput)
            except ReplayFixtureMissing:
                report.note("No offline recording for today's activity: stored the template.")
            else:
                digest, provider = result.value, result.provider
                report.note(
                    f"Digest by {result.model}" + (" (fallback)." if result.is_fallback else ".")
                )
        value: dict[str, object] = {"provider": provider, **digest.model_dump()}
        await sink.save(STATE_KEY, value | {"at": container.clock.now().isoformat()})
        report.items_new = 1

    return await run_job(JobName.DIGEST, job_run_log(container), container.clock, body)


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
