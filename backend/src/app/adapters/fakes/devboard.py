"""In-memory board stores and the in-process dispatcher (offline dev and tests, ARCH-04).

They implement the same contracts as the Postgres adapters: compare-and-set versions, one
active top-level run per task, atomic all-or-nothing lease claims, and a `run_steps`
notification for every stored step.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from app.domain.devboard.model import (
    COLUMN_ORDER,
    Column,
    LeaseRecord,
    NewStep,
    PlanRecord,
    RunRecord,
    RunStatus,
    StepRecord,
    TaskEventRecord,
    TaskRecord,
    TriageRecord,
)
from app.domain.devboard.paths import overlaps
from app.domain.errors import Conflict, NotFound
from app.domain.ports import Ids
from app.domain.ports.devboard import LeaseClaim, LeaseOutcome, RunFinish
from app.domain.steps import RUN_STEPS_CHANNEL


class Publisher(Protocol):
    async def publish(self, channel: str, payload: str) -> None: ...


class _Scoped:
    """Records the `user_id` each call ran under, like `app.user_id` for RLS."""

    def __init__(self) -> None:
        self.scopes: set[uuid.UUID] = set()

    def _scope(self, user_id: uuid.UUID) -> None:
        self.scopes.add(user_id)


def _board_key(task: TaskRecord) -> tuple[int, str, str]:
    return (COLUMN_ORDER.index(task.column), task.rank, str(task.id))


class InMemoryTaskStore(_Scoped):
    def __init__(self, runs: InMemoryRunStore | None = None) -> None:
        super().__init__()
        self._runs = runs
        self.tasks: dict[uuid.UUID, TaskRecord] = {}
        self.deleted: dict[uuid.UUID, datetime] = {}
        self.events_log: list[TaskEventRecord] = []
        self.triages: list[TriageRecord] = []
        self.plans: list[PlanRecord] = []

    async def insert(self, user_id: uuid.UUID, task: TaskRecord) -> None:
        self._scope(user_id)
        self.tasks[task.id] = task

    async def get(self, user_id: uuid.UUID, task_id: uuid.UUID) -> TaskRecord | None:
        self._scope(user_id)
        task = None if task_id in self.deleted else self.tasks.get(task_id)
        return None if task is None else self._view(task)

    def _view(self, task: TaskRecord) -> TaskRecord:
        """`active_run_id` is derived from runs, as the Postgres adapter does."""
        if self._runs is None:
            return task
        active = next(
            (
                run.id
                for run in self._runs.runs.values()
                if run.task_id == task.id and run.parent_run_id is None and not run.is_finished
            ),
            None,
        )
        return replace(task, active_run_id=active)

    async def list(
        self, user_id: uuid.UUID, columns: tuple[Column, ...], offset: int, limit: int
    ) -> list[TaskRecord]:
        self._scope(user_id)
        live = [task for task in self.tasks.values() if task.id not in self.deleted]
        chosen = [task for task in live if not columns or task.column in columns]
        return [self._view(task) for task in sorted(chosen, key=_board_key)][
            offset : offset + limit
        ]

    async def save(self, user_id: uuid.UUID, task: TaskRecord, expected_version: int) -> TaskRecord:
        self._scope(user_id)
        current = await self.get(user_id, task.id)
        if current is None:
            raise NotFound("Task not found.")
        if current.version != expected_version:
            raise Conflict("The card changed elsewhere.", current_version=current.version)
        stored = replace(task, version=current.version + 1)
        self.tasks[task.id] = stored
        return self._view(stored)

    async def delete(
        self, user_id: uuid.UUID, task_id: uuid.UUID, expected_version: int, at: datetime
    ) -> None:
        self._scope(user_id)
        current = await self.get(user_id, task_id)
        if current is None:
            raise NotFound("Task not found.")
        if current.version != expected_version:
            raise Conflict("The card changed elsewhere.", current_version=current.version)
        self.deleted[task_id] = at

    async def column_ranks(self, user_id: uuid.UUID, column: Column) -> list[tuple[str, uuid.UUID]]:
        self._scope(user_id)
        cards = await self.list(user_id, (column,), 0, 10_000)
        return [(task.rank, task.id) for task in cards]

    async def set_ranks(self, user_id: uuid.UUID, ranks: dict[uuid.UUID, str]) -> None:
        self._scope(user_id)
        for task_id, rank in ranks.items():
            task = self.tasks[task_id]
            self.tasks[task_id] = replace(task, rank=rank, version=task.version + 1)

    async def add_event(self, user_id: uuid.UUID, event: TaskEventRecord) -> None:
        self._scope(user_id)
        self.events_log.append(event)

    async def events(
        self, user_id: uuid.UUID, task_id: uuid.UUID, offset: int, limit: int
    ) -> list[TaskEventRecord]:
        self._scope(user_id)
        mine = [event for event in self.events_log if event.task_id == task_id]
        return list(reversed(mine))[offset : offset + limit]

    async def add_triage(self, user_id: uuid.UUID, triage: TriageRecord) -> None:
        self._scope(user_id)
        self.triages.append(triage)

    async def latest_triage(self, user_id: uuid.UUID, task_id: uuid.UUID) -> TriageRecord | None:
        self._scope(user_id)
        return next((t for t in reversed(self.triages) if t.task_id == task_id), None)

    async def add_plan(self, user_id: uuid.UUID, plan: PlanRecord) -> None:
        self._scope(user_id)
        self.plans.append(plan)

    async def latest_plan(self, user_id: uuid.UUID, task_id: uuid.UUID) -> PlanRecord | None:
        self._scope(user_id)
        return next((p for p in reversed(self.plans) if p.task_id == task_id), None)

    async def save_plan(
        self, user_id: uuid.UUID, plan: PlanRecord, expected_version: int
    ) -> PlanRecord:
        self._scope(user_id)
        index = next((i for i, p in enumerate(self.plans) if p.id == plan.id), None)
        if index is None:
            raise NotFound("Plan not found.")
        current = self.plans[index]
        if current.version != expected_version:
            raise Conflict("The plan changed elsewhere.", current_version=current.version)
        stored = replace(plan, version=current.version + 1)
        self.plans[index] = stored
        return stored


class InMemoryRunStore(_Scoped):
    def __init__(self, ids: Ids, publisher: Publisher | None = None) -> None:
        super().__init__()
        self._ids = ids
        self._publisher = publisher
        self.runs: dict[uuid.UUID, RunRecord] = {}
        self.steps: dict[uuid.UUID, list[StepRecord]] = {}

    async def insert(self, user_id: uuid.UUID, run: RunRecord) -> None:
        self._scope(user_id)
        if run.parent_run_id is None and any(
            other.task_id == run.task_id and other.parent_run_id is None and not other.is_finished
            for other in self.runs.values()
        ):
            raise Conflict("This card already has an active run.")
        self.runs[run.id] = run
        self.steps[run.id] = []

    async def get(self, user_id: uuid.UUID, run_id: uuid.UUID) -> RunRecord | None:
        self._scope(user_id)
        return self.runs.get(run_id)

    async def for_task(
        self, user_id: uuid.UUID, task_id: uuid.UUID, offset: int, limit: int
    ) -> list[RunRecord]:
        self._scope(user_id)
        mine = [run for run in self.runs.values() if run.task_id == task_id]
        ordered = sorted(mine, key=lambda run: (run.created_at, str(run.id)), reverse=True)
        return ordered[offset : offset + limit]

    async def active(self, user_id: uuid.UUID, task_id: uuid.UUID) -> list[RunRecord]:
        self._scope(user_id)
        return [r for r in self.runs.values() if r.task_id == task_id and not r.is_finished]

    async def mark_running(self, user_id: uuid.UUID, run_id: uuid.UUID, at: datetime) -> None:
        self._scope(user_id)
        run = self.runs[run_id]
        if run.status is RunStatus.QUEUED:
            self.runs[run_id] = replace(run, status=RunStatus.RUNNING, started_at=at)

    async def append_step(
        self, user_id: uuid.UUID, run_id: uuid.UUID, step: NewStep, at: datetime
    ) -> StepRecord:
        self._scope(user_id)
        steps = self.steps[run_id]
        record = StepRecord(
            id=self._ids.new_id(),
            run_id=run_id,
            seq=len(steps) + 1,
            kind=step.kind,
            summary=step.summary,
            at=at,
            agent=step.agent,
            tool=step.tool,
        )
        steps.append(record)
        if self._publisher is not None:
            payload = {"run_id": str(run_id), "seq": record.seq, "id": str(record.id)}
            await self._publisher.publish(RUN_STEPS_CHANNEL, json.dumps(payload))
        return record

    async def finish(
        self, user_id: uuid.UUID, run_id: uuid.UUID, finish: RunFinish, final: NewStep
    ) -> RunRecord | None:
        self._scope(user_id)
        run = self.runs[run_id]
        if run.is_finished:
            return None
        done = replace(
            run,
            status=finish.status,
            outcome=finish.outcome,
            finished_at=finish.at,
            started_at=run.started_at or finish.at,
            cost_usd=run.cost_usd + finish.cost_usd,
            model=finish.model or run.model,
            provider=finish.provider or run.provider,
        )
        self.runs[run_id] = done
        await self.append_step(user_id, run_id, final, finish.at)
        return done

    async def steps_after(
        self, user_id: uuid.UUID, run_id: uuid.UUID, after_seq: int, limit: int
    ) -> list[StepRecord]:
        self._scope(user_id)
        return [step for step in self.steps.get(run_id, []) if step.seq > after_seq][:limit]


class InMemoryLeaseStore(_Scoped):
    def __init__(self, ids: Ids) -> None:
        super().__init__()
        self._ids = ids
        self.leases: dict[uuid.UUID, LeaseRecord] = {}
        self.waits: list[tuple[uuid.UUID, str, datetime]] = []

    async def claim(self, user_id: uuid.UUID, claim: LeaseClaim) -> LeaseOutcome:
        self._scope(user_id)
        others = [
            lease
            for lease in self.leases.values()
            if lease.is_active(claim.at) and lease.run_id != claim.run_id
        ]
        conflicts = tuple(
            lease for lease in others if any(overlaps(lease.path, p) for p, _ in claim.paths)
        )
        if conflicts:
            self.waits.extend((claim.run_id, path, claim.at) for path, _ in claim.paths)
            return LeaseOutcome(granted=(), conflicts=conflicts)
        held = {
            lease.path: lease
            for lease in self.leases.values()
            if lease.run_id == claim.run_id and lease.is_active(claim.at)
        }
        granted: list[LeaseRecord] = []
        for path, kind in claim.paths:
            lease = held.get(path) or LeaseRecord(
                id=self._ids.new_id(),
                run_id=claim.run_id,
                task_id=claim.task_id,
                path=path,
                kind=kind,
                granted_at=claim.at,
                expires_at=claim.expires_at,
            )
            lease = replace(lease, expires_at=max(lease.expires_at, claim.expires_at))
            self.leases[lease.id] = lease
            granted.append(lease)
        return LeaseOutcome(granted=tuple(granted), conflicts=())

    async def renew(
        self, user_id: uuid.UUID, run_id: uuid.UUID, at: datetime, expires_at: datetime
    ) -> int:
        self._scope(user_id)
        renewed = 0
        for lease in list(self.leases.values()):
            if lease.run_id == run_id and lease.is_active(at):
                self.leases[lease.id] = replace(lease, heartbeat_at=at, expires_at=expires_at)
                renewed += 1
        return renewed

    async def release(
        self,
        user_id: uuid.UUID,
        at: datetime,
        *,
        run_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
    ) -> int:
        self._scope(user_id)
        released = 0
        for lease in list(self.leases.values()):
            is_match = (run_id is not None and lease.run_id == run_id) or (
                task_id is not None and lease.task_id == task_id
            )
            if is_match and lease.released_at is None:
                self.leases[lease.id] = replace(lease, released_at=at)
                released += 1
        return released

    async def reclaim_expired(self, user_id: uuid.UUID, at: datetime) -> int:
        self._scope(user_id)
        reclaimed = 0
        for lease in list(self.leases.values()):
            if lease.released_at is None and lease.expires_at <= at:
                self.leases[lease.id] = replace(lease, released_at=at)
                reclaimed += 1
        return reclaimed

    async def active(self, user_id: uuid.UUID, at: datetime) -> list[LeaseRecord]:
        self._scope(user_id)
        return sorted(
            (lease for lease in self.leases.values() if lease.is_active(at)),
            key=lambda lease: lease.path,
        )


class InProcessDispatcher:
    """asyncio tasks keyed by run id. `drain` lets tests wait for background work."""

    def __init__(self) -> None:
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self.failures: list[BaseException] = []

    def spawn(self, key: uuid.UUID, work: Callable[[], Awaitable[None]]) -> None:
        async def runner() -> None:
            await work()

        task = asyncio.get_running_loop().create_task(runner())
        self._tasks[key] = task
        task.add_done_callback(lambda done: self._finished(key, done))

    def cancel(self, key: uuid.UUID) -> bool:
        task = self._tasks.get(key)
        return task.cancel() if task is not None else False

    async def drain(self) -> None:
        """Wait until no background work is left (work may spawn more work)."""
        while True:
            for key, task in list(self._tasks.items()):
                if task.done():
                    self._finished(key, task)
            if not self._tasks:
                return
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    def _finished(self, key: uuid.UUID, task: asyncio.Task[None]) -> None:
        # Idempotent: the done-callback and `drain` may both reap the same task.
        if self._tasks.get(key) is not task:
            return
        del self._tasks[key]
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                self.failures.append(error)
