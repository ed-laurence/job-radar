"""The board's automation: drops that start work, and the kill switch.

- create / edit text / `out_of_scope`->`backlog`: triage (then `triaged` or `out_of_scope`)
- `triaged`->`plan_review`: the read-only dev-lead plan plus the test-only step
- `ready`->`agent_dev`: the dev run (simulated until M2), then `agent_review`

Work runs in the background through the `Dispatcher`; every step is stored and streamed.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from app.domain.devboard.board import BoardService
from app.domain.devboard.leases import LeaseService
from app.domain.devboard.model import (
    Actor,
    Column,
    EventKind,
    RunKind,
    RunRecord,
    RunStatus,
    TaskRecord,
    TriageRecord,
)
from app.domain.devboard.plans import PlanService
from app.domain.devboard.runs import RunService, RunTools
from app.domain.devboard.triage_policy import finalise
from app.domain.errors import Conflict, DomainError, ValidationFailed
from app.domain.ports import Clock, Ids
from app.domain.ports.devboard import (
    AgentResult,
    AgentRunner,
    Dispatcher,
    RunContext,
    RunStore,
    TaskStore,
    TriageAgent,
)

TRIAGE_COLUMNS = frozenset({Column.BACKLOG, Column.TRIAGED, Column.OUT_OF_SCOPE})
_AGENTS = {RunKind.TRIAGE: "triage", RunKind.PLAN: "dev-lead", RunKind.DEV: "dev-lead"}

type Work = Callable[[Actor, RunRecord], Awaitable[None]]


@dataclass(frozen=True)
class Stores:
    tasks: TaskStore
    runs: RunStore


@dataclass(frozen=True)
class Agents:
    triage: TriageAgent
    runner: AgentRunner


class Workflow:
    def __init__(
        self,
        stores: Stores,
        services: tuple[BoardService, RunService, PlanService, LeaseService],
        agents: Agents,
        dispatcher: Dispatcher,
        system: tuple[Clock, Ids],
    ) -> None:
        self._tasks, self._runs = stores.tasks, stores.runs
        self._board, self._run_service, self._plans, self._leases = services
        self._agents = agents
        self._dispatcher = dispatcher
        self._clock, self._ids = system

    # ------------------------------------------------------------ BoardHooks
    async def on_created(self, actor: Actor, task: TaskRecord) -> None:
        await self.queue_triage(actor, task.id)

    async def on_text_changed(self, actor: Actor, task: TaskRecord) -> None:
        if task.column in TRIAGE_COLUMNS and task.active_run_id is None:
            await self.queue_triage(actor, task.id)

    async def on_moved(self, actor: Actor, task: TaskRecord, from_column: Column) -> None:
        if from_column is Column.OUT_OF_SCOPE and task.column is Column.BACKLOG:
            await self.queue_triage(actor, task.id)
        elif task.column is Column.PLAN_REVIEW:
            await self.request_plan(actor, task.id)
        elif task.column is Column.AGENT_DEV:
            await self._launch(actor, task, RunKind.DEV, self._develop)

    # ------------------------------------------------------------ commands
    async def queue_triage(self, actor: Actor, task_id: uuid.UUID) -> RunRecord:
        task = await self._board.get(actor, task_id)
        if task.column not in TRIAGE_COLUMNS:
            raise ValidationFailed("Only Backlog, Triaged or Out of scope cards are triaged.")
        run = await self._launch(actor, task, RunKind.TRIAGE, self._triage, start=False)
        if task.column is not Column.BACKLOG:
            await self._board.system_move(actor.as_system(), task_id, Column.BACKLOG, run_id=run.id)
        self._start(actor, run, self._triage)
        return run

    async def request_plan(self, actor: Actor, task_id: uuid.UUID) -> RunRecord:
        task = await self._board.get(actor, task_id)
        if task.column is not Column.PLAN_REVIEW:
            raise ValidationFailed("Plans are made for cards in Plan review.")
        return await self._launch(actor, task, RunKind.PLAN, self._plan)

    async def kill(
        self, actor: Actor, task_id: uuid.UUID, version: int, reason: str | None
    ) -> TaskRecord:
        """Stop every active run of the card, release its leases, move it to `blocked`."""
        task = await self._board.get(actor, task_id)
        active = await self._runs.active(actor.user_id, task_id)
        if not active and task.column in {Column.BLOCKED, Column.DONE}:
            return task
        if task.version != version:
            raise Conflict("The card changed elsewhere.", current_version=task.version)
        why = (reason or "").strip() or "Stopped with the kill switch."
        for run in active:
            self._dispatcher.cancel(run.id)
            await self._run_service.finish(actor, run, RunStatus.KILLED, why)
        await self._leases.release_task(actor, task_id)
        if task.column is not Column.BLOCKED and task.column is not Column.DONE:
            task = await self._board.system_move(actor, task_id, Column.BLOCKED, reason=why)
        await self._board.record(actor, task, EventKind.KILLED, active[0].id if active else None)
        return await self._board.get(actor, task_id)

    # ------------------------------------------------------------ execution
    async def _launch(
        self, actor: Actor, task: TaskRecord, kind: RunKind, work: Work, *, start: bool = True
    ) -> RunRecord:
        run = RunRecord(
            id=self._ids.new_id(),
            task_id=task.id,
            kind=kind,
            status=RunStatus.QUEUED,
            created_at=self._clock.now(),
            agent=_AGENTS.get(kind),
        )
        await self._runs.insert(actor.user_id, run)
        await self._board.record(actor, task, EventKind.RUN_STARTED, run.id)
        if start:
            self._start(actor, run, work)
        return run

    def _start(self, actor: Actor, run: RunRecord, work: Work) -> None:
        async def execute() -> None:
            await self._runs.mark_running(actor.user_id, run.id, self._clock.now())
            try:
                await work(actor, run)
            except asyncio.CancelledError:
                await self._run_service.finish(actor, run, RunStatus.KILLED, "Stopped.")
                raise
            except DomainError as error:
                await self._run_service.finish(
                    actor, run, RunStatus.FAILED, error.detail or "The run failed."
                )
            await self._finished_event(actor, run)

        self._dispatcher.spawn(run.id, execute)

    async def _finished_event(self, actor: Actor, run: RunRecord) -> None:
        task = await self._tasks.get(actor.user_id, run.task_id)
        if task is not None:
            await self._board.record(actor.as_system(), task, EventKind.RUN_FINISHED, run.id)

    async def _context(self, actor: Actor, run: RunRecord) -> RunContext:
        task = await self._board.get(actor, run.task_id)
        return RunContext(
            run=run,
            task=task,
            actor=actor,
            plan=await self._tasks.latest_plan(actor.user_id, task.id),
            triage=await self._tasks.latest_triage(actor.user_id, task.id),
        )

    def _tools(self, actor: Actor, run: RunRecord) -> RunTools:
        return RunTools(self._run_service, self._leases, actor.as_agent(), run)

    async def _triage(self, actor: Actor, run: RunRecord) -> None:
        context = await self._context(actor, run)
        decision = await self._agents.triage.triage(context, self._tools(actor, run))
        record = finalise(context.task.id, decision, self._clock.now())
        await self._tasks.add_triage(actor.user_id, record)
        agent = actor.as_agent()
        target = Column.TRIAGED if record.in_scope else Column.OUT_OF_SCOPE
        moved = await self._board.system_move(
            agent,
            run.task_id,
            target,
            run_id=run.id,
            change=lambda task: _apply_triage(task, record),
        )
        await self._board.record(agent, moved, EventKind.TRIAGED, run.id)
        outcome = "In scope." if record.in_scope else f"Out of scope: {record.out_of_scope_reason}"
        await self._run_service.finish(
            actor,
            run,
            RunStatus.SUCCEEDED,
            outcome,
            cost_usd=decision.cost_usd,
            model=decision.model,
            provider=decision.provider,
        )

    async def _plan(self, actor: Actor, run: RunRecord) -> None:
        context = await self._context(actor, run)
        result = await self._agents.runner.run(context, self._tools(actor, run))
        if result.is_success and result.plan is not None:
            await self._plans.save_draft(actor.as_agent(), context.task, result.plan)
        await self._finish_result(actor, run, result)

    async def _develop(self, actor: Actor, run: RunRecord) -> None:
        context = await self._context(actor, run)
        tools = self._tools(actor, run)
        try:
            result = await self._agents.runner.run(context, tools)
        except DomainError as error:
            result = AgentResult(is_success=False, outcome=error.detail or "The dev run failed.")
        finally:
            await tools.release_paths()
        agent = actor.as_agent()
        # A killed run's card is already `blocked`, so these moves fail and change nothing.
        if result.is_success:
            await self._board.system_move(agent, run.task_id, Column.AGENT_REVIEW, run_id=run.id)
        else:
            await self._board.system_move(
                agent, run.task_id, Column.BLOCKED, reason=result.outcome, run_id=run.id
            )
        await self._finish_result(actor, run, result)

    async def _finish_result(self, actor: Actor, run: RunRecord, result: AgentResult) -> None:
        status = RunStatus.SUCCEEDED if result.is_success else RunStatus.FAILED
        await self._run_service.finish(
            actor,
            run,
            status,
            result.outcome,
            cost_usd=result.cost_usd,
            model=result.model,
            provider=result.provider,
        )


def _apply_triage(task: TaskRecord, triage: TriageRecord) -> TaskRecord:
    return replace(
        task,
        type=triage.type,
        size=triage.size,
        risk=triage.risk,
        areas=triage.areas,
        needs_human_design=triage.needs_human_design,
        out_of_scope_reason=triage.out_of_scope_reason,
    )
