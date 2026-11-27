"""Agent runs and their steps: reads, the step stream, and the tools a running agent gets.

Steps are redacted before they are stored (SEC-06), so a replayed or live step is already
safe. The stream is the shared step-stream mechanism in `app.domain.steps` (ARCH-31).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.domain.devboard.board import decode_cursor, encode_cursor
from app.domain.devboard.leases import LeaseService
from app.domain.devboard.model import (
    Actor,
    NewStep,
    Page,
    RunRecord,
    RunStatus,
    StepKind,
    StepRecord,
)
from app.domain.errors import NotFound
from app.domain.ports import Clock, NotificationHub
from app.domain.ports.devboard import LeaseOutcome, RunFinish, RunStore, TaskStore, TextRedactor
from app.domain.steps import Follow, StepSource, follow_steps

MAX_STEP_CHARS = 4000
STEP_BATCH = 200
POLL_SECONDS = 5.0


@dataclass(frozen=True)
class StreamEnd:
    run: RunRecord


class RunService:
    def __init__(
        self,
        runs: RunStore,
        tasks: TaskStore,
        hub: NotificationHub,
        redactor: TextRedactor,
        clock: Clock,
        *,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._runs = runs
        self._tasks = tasks
        self._hub = hub
        self._redactor = redactor
        self._clock = clock
        self._poll = poll_seconds

    async def get(self, actor: Actor, run_id: uuid.UUID) -> RunRecord:
        run = await self._runs.get(actor.user_id, run_id)
        if run is None:
            raise NotFound("Run not found.")
        return run

    async def for_task(
        self, actor: Actor, task_id: uuid.UUID, cursor: str | None, limit: int
    ) -> Page[RunRecord]:
        if await self._tasks.get(actor.user_id, task_id) is None:
            raise NotFound("Task not found.")
        offset = decode_cursor(cursor)
        rows = await self._runs.for_task(actor.user_id, task_id, offset, limit + 1)
        more = len(rows) > limit
        return Page(items=rows[:limit], next_cursor=encode_cursor(offset + limit) if more else None)

    async def append(self, actor: Actor, run_id: uuid.UUID, step: NewStep) -> StepRecord:
        clean = await self._redactor.redact(step.summary)
        safe = NewStep(
            kind=step.kind, summary=clean[:MAX_STEP_CHARS], agent=step.agent, tool=step.tool
        )
        return await self._runs.append_step(actor.user_id, run_id, safe, self._clock.now())

    async def finish(
        self,
        actor: Actor,
        run: RunRecord,
        status: RunStatus,
        outcome: str,
        *,
        cost_usd: float = 0.0,
        model: str | None = None,
        provider: str | None = None,
    ) -> RunRecord | None:
        clean = (await self._redactor.redact(outcome))[:1000]
        finish = RunFinish(
            status=status,
            outcome=clean,
            at=self._clock.now(),
            cost_usd=cost_usd,
            model=model,
            provider=provider,
        )
        final = NewStep(
            kind=StepKind.STATUS, summary=f"Run {status.value}: {clean}", agent=run.agent
        )
        return await self._runs.finish(actor.user_id, run.id, finish, final)

    async def stream(
        self, actor: Actor, run_id: uuid.UUID, after_seq: int
    ) -> AsyncIterator[StepRecord | StreamEnd]:
        """Saved steps after `after_seq`, then live ones, then `StreamEnd`."""
        await self.get(actor, run_id)

        async def read_after(seq: int) -> list[StepRecord]:
            return await self._runs.steps_after(actor.user_id, run_id, seq, STEP_BATCH)

        async def ended() -> StreamEnd | None:
            run = await self.get(actor, run_id)
            return StreamEnd(run) if run.is_finished else None

        source = StepSource(run_id, read_after, ended, batch=STEP_BATCH)
        async for item in follow_steps(self._hub, source, after_seq, Follow(self._poll)):
            yield item


class RunTools:
    """The coordinator surface for one run (M1: in-process; M2: the coordinator service)."""

    def __init__(
        self, service: RunService, leases: LeaseService, actor: Actor, run: RunRecord
    ) -> None:
        self._service = service
        self._leases = leases
        self._actor = actor
        self._run = run

    async def emit(self, step: NewStep) -> StepRecord:
        agent = step.agent or self._run.agent
        return await self._service.append(
            self._actor, self._run.id, NewStep(step.kind, step.summary, agent, step.tool)
        )

    async def claim_paths(self, paths: tuple[str, ...]) -> LeaseOutcome:
        return await self._leases.claim(self._actor, self._run.id, self._run.task_id, paths)

    async def renew_leases(self) -> int:
        return await self._leases.heartbeat(self._actor, self._run.id)

    async def release_paths(self) -> int:
        return await self._leases.release_run(self._actor, self._run.id)

    async def is_cancelled(self) -> bool:
        current = await self._service.get(self._actor, self._run.id)
        return current.status is RunStatus.KILLED
