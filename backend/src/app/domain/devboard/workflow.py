"""The board's automation: drops that start work, and the kill switch.

- create / edit text / `out_of_scope`->`backlog`: triage (then `triaged` or `out_of_scope`);
  the route puts an in-scope card at the top (`next`) or bottom (`later`) of `triaged`, or
  (`now`) moves it on to `plan_review` and starts the plan as a maintainer's drag would
- `triaged`->`plan_review`: the read-only dev-lead plan plus the test-only step
- `ready`->`agent_dev`: the dev run, then `agent_review`, which starts the review run
- review verdicts (`review_flow`): `approved` and `needs_decision` -> `human_review`;
  `changes_requested` -> a rework dev run with the blockers (back to `agent_dev`)
- `human_review`->`changes_requested` (a maintainer, with notes): the same rework run

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
    ReviewVerdict,
    Route,
    RunKind,
    RunRecord,
    RunStatus,
    TaskRecord,
    TriageRecord,
)
from app.domain.devboard.placement import place_first, place_last
from app.domain.devboard.plans import PlanService, plan_paths
from app.domain.devboard.repo_paths import unknown_paths
from app.domain.devboard.review_flow import ReviewFlow, ReviewInputs, without_needs_decision
from app.domain.devboard.runs import RunService, RunTools
from app.domain.devboard.triage_policy import finalise, shown_paths, triage_problem
from app.domain.errors import Conflict, DomainError, UpstreamUnavailable, ValidationFailed
from app.domain.ports import Clock, Ids
from app.domain.ports.devboard import (
    AgentResult,
    AgentRunner,
    CardRef,
    Dispatcher,
    PlanDraft,
    RepoFiles,
    RunContext,
    RunStore,
    TaskStore,
    TriageAgent,
)

TRIAGE_COLUMNS = frozenset({Column.BACKLOG, Column.TRIAGED, Column.OUT_OF_SCOPE})
# Triage sees the other open cards (for its duplicate check), at most this many.
MAX_OPEN_CARDS = 50
_OPEN_COLUMNS = tuple(column for column in Column if column is not Column.DONE)
_AGENTS = {
    RunKind.TRIAGE: "triage",
    RunKind.PLAN: "dev-lead",
    RunKind.DEV: "dev-lead",
    RunKind.REVIEW: "reviewer",
}

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
        *,
        repo_files: RepoFiles,
        review: ReviewInputs | None = None,
    ) -> None:
        self._tasks, self._runs = stores.tasks, stores.runs
        self._repo_files = repo_files
        self._board, self._run_service, self._plans, self._leases = services
        self._agents = agents
        self._dispatcher = dispatcher
        self._clock, self._ids = system
        self._reviews = ReviewFlow(self._board, self._tasks, review or ReviewInputs(), system)

    # ------------------------------------------------------------ BoardHooks
    async def on_created(self, actor: Actor, task: TaskRecord) -> None:
        await self.queue_triage(actor, task.id)

    async def on_text_changed(self, actor: Actor, task: TaskRecord) -> None:
        if task.column in TRIAGE_COLUMNS and task.active_run_id is None:
            await self.queue_triage(actor, task.id)

    async def on_moved(
        self, actor: Actor, task: TaskRecord, from_column: Column, *, reason: str | None = None
    ) -> None:
        if from_column is Column.HUMAN_REVIEW and task.column is Column.CHANGES_REQUESTED:
            await self._rework(actor, task.id, notes=reason or "")
        elif from_column is Column.OUT_OF_SCOPE and task.column is Column.BACKLOG:
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
        self,
        actor: Actor,
        task: TaskRecord,
        kind: RunKind,
        work: Work,
        *,
        start: bool = True,
        review_round: int = 0,
    ) -> RunRecord:
        run = RunRecord(
            id=self._ids.new_id(),
            task_id=task.id,
            kind=kind,
            status=RunStatus.QUEUED,
            created_at=self._clock.now(),
            agent=_AGENTS.get(kind),
            round=review_round,
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
        is_triage = run.kind is RunKind.TRIAGE
        # Review runs see the previous round; rework dev runs (round > 0) the blockers to fix.
        sees_review = run.kind is RunKind.REVIEW or (run.kind is RunKind.DEV and run.round > 0)
        return RunContext(
            run=run,
            task=task,
            actor=actor,
            plan=await self._tasks.latest_plan(actor.user_id, task.id),
            triage=await self._tasks.latest_triage(actor.user_id, task.id),
            open_cards=await self._open_cards(actor, task.id) if is_triage else (),
            review=await self._tasks.latest_review(actor.user_id, task.id) if sees_review else None,
        )

    async def _open_cards(self, actor: Actor, task_id: uuid.UUID) -> tuple[CardRef, ...]:
        """Other live cards not yet done, in board order."""
        listed = await self._tasks.list(actor.user_id, _OPEN_COLUMNS, 0, MAX_OPEN_CARDS + 1)
        others = (CardRef(t.id, t.title, t.column) for t in listed if t.id != task_id)
        return tuple(others)[:MAX_OPEN_CARDS]

    def _tools(self, actor: Actor, run: RunRecord) -> RunTools:
        return RunTools(self._run_service, self._leases, actor.as_agent(), run)

    async def _triage(self, actor: Actor, run: RunRecord) -> None:
        context = await self._context(actor, run)
        decision = await self._agents.triage.triage(context, self._tools(actor, run))
        now = self._clock.now()
        record = finalise(context.task.id, decision, now)
        files = await self._repo_files.paths() if record.in_scope else None
        problem = triage_problem(decision, files, (card.id for card in context.open_cards))
        if problem is not None:
            raise ValidationFailed(problem)
        await self._tasks.add_triage(actor.user_id, record)
        agent = actor.as_agent()
        moved = await self._board.system_move(
            agent,
            run.task_id,
            Column.TRIAGED if record.in_scope else Column.OUT_OF_SCOPE,
            run_id=run.id,
            change=lambda task: _apply_triage(task, record),
            rank=await self._triaged_rank(actor, record),
        )
        await self._board.record(agent, moved, EventKind.TRIAGED, run.id)
        await self._run_service.finish(
            actor,
            run,
            RunStatus.SUCCEEDED,
            _triage_outcome(record),
            cost_usd=decision.cost_usd,
            model=decision.model,
            provider=decision.provider,
        )
        if record.in_scope and record.route is Route.NOW:
            await self._board.system_move(agent, run.task_id, Column.PLAN_REVIEW, run_id=run.id)
            await self.request_plan(actor, run.task_id)

    async def _triaged_rank(self, actor: Actor, record: TriageRecord) -> str | None:
        """`next` and `now` go to the top of Triaged, `later` to the bottom."""
        if not record.in_scope:
            return None
        place = place_last if record.route is Route.LATER else place_first
        return await place(self._tasks, actor.user_id, Column.TRIAGED, record.task_id)

    async def _plan(self, actor: Actor, run: RunRecord) -> None:
        context = await self._context(actor, run)
        result = await self._agents.runner.run(context, self._tools(actor, run))
        if result.is_success and result.plan is not None:
            try:
                await self._check_plan_paths(result.plan)
                await self._plans.save_draft(actor.as_agent(), context.task, result.plan)
            except (ValidationFailed, UpstreamUnavailable) as refused:
                # Code refused the draft: the run fails with the reason and keeps its spend.
                reason = refused.detail or "The plan was refused."
                result = replace(result, is_success=False, outcome=reason)
        await self._finish_result(actor, run, result)

    async def _check_plan_paths(self, draft: PlanDraft) -> None:
        unknown = unknown_paths(plan_paths(draft), await self._repo_files.paths())
        if unknown:
            raise ValidationFailed(
                f"The plan named paths that are not in the repo: {shown_paths(unknown)}"
            )

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
        # Move the card only if it is still in `agent_dev`: a request_help, the kill switch or a
        # human may have moved it while the run worked. The run still finishes with its outcome
        # and spend either way.
        task = await self._tasks.get(actor.user_id, run.task_id)
        in_review: TaskRecord | None = None
        if task is not None and task.column is Column.AGENT_DEV:
            if result.is_success:
                in_review = await self._board.system_move(
                    agent,
                    run.task_id,
                    Column.AGENT_REVIEW,
                    run_id=run.id,
                    change=lambda card: _published(card, result),
                )
            else:
                await self._board.system_move(
                    agent, run.task_id, Column.BLOCKED, reason=result.outcome, run_id=run.id
                )
        await self._finish_result(actor, run, result)
        if in_review is not None:
            next_round = in_review.review_round + 1
            await self._launch(
                actor, in_review, RunKind.REVIEW, self._review, review_round=next_round
            )

    async def _review(self, actor: Actor, run: RunRecord) -> None:
        context = await self._context(actor, run)
        try:
            result = await self._agents.runner.run(context, self._tools(actor, run))
        except DomainError as error:
            result = AgentResult(is_success=False, outcome=error.detail or "The review failed.")
        outcome = await self._reviews.conclude(actor, run, result)
        await self._finish_result(actor, run, outcome.result)
        if outcome.verdict is ReviewVerdict.CHANGES_REQUESTED:
            await self._rework(actor, run.task_id, notes=None)

    async def _rework(self, actor: Actor, task_id: uuid.UUID, *, notes: str | None) -> None:
        """From `changes_requested`: a fresh dev run with the same plan and leases, given the
        latest review (the agent's, or the human's `notes` stored as one)."""
        task = await self._board.get(actor, task_id)
        run = await self._launch(
            actor, task, RunKind.DEV, self._develop, start=False, review_round=task.review_round
        )
        if notes is not None:
            await self._reviews.add_notes(actor, task, run, notes)
        agent = actor.as_agent()
        await self._board.system_move(
            agent, task_id, Column.AGENT_DEV, run_id=run.id, change=without_needs_decision
        )
        self._start(actor, run, self._develop)

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
        priority=triage.priority,
        severity=triage.severity,
        route=triage.route,
        out_of_scope_reason=triage.out_of_scope_reason,
    )


def _published(task: TaskRecord, result: AgentResult) -> TaskRecord:
    """A dev run's branch replaces the card's; a run that published none keeps the old one.
    Its GitHub branch replaces the card's too (None when it pushed nothing)."""
    if not result.branch:
        return task
    return replace(task, branch=result.branch, base_sha=result.base_sha, pr_branch=result.pr_branch)


def _triage_outcome(triage: TriageRecord) -> str:
    if not triage.in_scope:
        return f"Out of scope: {triage.out_of_scope_reason}"
    rating = f"{triage.priority.value}, {triage.severity.value}"
    return f"In scope: {rating}, route {triage.route.value}."
