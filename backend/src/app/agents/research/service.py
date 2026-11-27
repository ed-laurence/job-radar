"""Research runs: start (queued, then run in the background), read, and follow the steps.

Steps live in the shared `run_steps` table and stream through `app.domain.steps`, the same
mechanism as dev-board runs."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.agents.research.agent import ResearchAgent
from app.domain.errors import NotFound
from app.domain.identity import Principal
from app.domain.jobs.crm import TargetService
from app.domain.jobs.ports import ResearchStore
from app.domain.jobs.records import ResearchRunRecord, StepRecord
from app.domain.ports import Clock, Ids, NotificationHub
from app.domain.steps import Follow, StepSource, follow_steps

FOCUS = ("overview", "hiring_signals", "people_to_approach", "news")
TERMINAL = frozenset({"succeeded", "failed", "killed", "timed_out"})


@dataclass(frozen=True)
class StreamTiming:
    """How the step stream follows a live run: poll interval and the longest it will wait."""

    poll_seconds: float = 0.5
    max_polls: int = 1200


@dataclass(frozen=True)
class RunEnded:
    run: ResearchRunRecord


class ResearchService:
    def __init__(
        self,
        targets: TargetService,
        store: ResearchStore,
        agent: ResearchAgent,
        clock: Clock,
        ids: Ids,
        timing: StreamTiming | None = None,
        hub: NotificationHub | None = None,
    ) -> None:
        self._targets = targets
        self._store = store
        self._agent = agent
        self._clock = clock
        self._ids = ids
        self._timing = timing or StreamTiming()
        # The shared `run_steps` hub wakes the stream on new steps; without one it polls.
        self._hub = hub

    async def start(
        self, principal: Principal, target_id: uuid.UUID, focus: tuple[str, ...]
    ) -> ResearchRunRecord:
        await self._targets.get_target(principal, target_id)
        chosen = [item for item in FOCUS if item in focus] or ["overview"]
        values: dict[str, object] = {
            "id": self._ids.new_id(),
            "target_id": target_id,
            "status": "queued",
            "focus": chosen,
            "sources": [],
            "created_at": self._clock.now(),
        }
        return await self._store.create_run(principal.user_id, values)

    async def execute(self, principal: Principal, run_id: uuid.UUID) -> None:
        run = await self.get_run(principal, run_id)
        target = await self._targets.get_target(principal, run.target_id)
        await self._agent.run(principal.user_id, run, target)

    async def get_run(self, principal: Principal, run_id: uuid.UUID) -> ResearchRunRecord:
        run = await self._store.get_run(principal.user_id, run_id)
        if run is None:
            raise NotFound("That research run doesn't exist.")
        return run

    async def open_steps(
        self, principal: Principal, run_id: uuid.UUID, after: int
    ) -> AsyncIterator[StepRecord | RunEnded]:
        """Check the run exists (raises before streaming), then replay and follow its steps."""
        await self.get_run(principal, run_id)
        return self._follow(principal, run_id, after)

    async def _follow(
        self, principal: Principal, run_id: uuid.UUID, after: int
    ) -> AsyncIterator[StepRecord | RunEnded]:
        async def read_after(seq: int) -> list[StepRecord]:
            return await self._store.steps_after(principal.user_id, run_id, seq)

        async def ended() -> RunEnded | None:
            run = await self.get_run(principal, run_id)
            return RunEnded(run) if run.status in TERMINAL else None

        async def give_up() -> RunEnded:
            return RunEnded(await self.get_run(principal, run_id))

        source = StepSource(run_id, read_after, ended)
        follow = Follow(self._timing.poll_seconds, max_waits=self._timing.max_polls)
        async for item in follow_steps(self._hub, source, after, follow, give_up):
            yield item
