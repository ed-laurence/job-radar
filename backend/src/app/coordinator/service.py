"""The coordinator's tools over the dev-board domain services and the lease/run stores.

Every call starts from a `Scope` built from a verified token: the task, its owner (who scopes
row-level security, SEC-03) and the run, which must be one of the task's active runs. No tool
takes a task or run id, so none can reach another task.

Results are plain JSON values; `app.coordinator.server` redacts and caps them.

Review runs (the sandboxed reviewer) are read-only and see an isolated task page
(`task_pages.review_header`): never the writer's steps or handoff. The dev run's test counts
come from its outcome, which the runner builds from the sandbox's own test re-run. Rework dev
runs (the card has a branch and the latest review asked for changes) get `review_blockers`.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.coordinator import task_pages as pages
from app.coordinator.schemas import AgentHandoff, parse_capped
from app.coordinator.task_pages import Json
from app.coordinator.tokens import Grant
from app.domain.devboard.board import BoardService
from app.domain.devboard.leases import LeaseService, lease_targets
from app.domain.devboard.messages import RequestHelp
from app.domain.devboard.model import (
    Actor,
    ActorKind,
    Column,
    LeaseKind,
    NewStep,
    PlanRecord,
    ReviewFinding,
    ReviewVerdict,
    RunKind,
    RunRecord,
    RunStatus,
    StepKind,
    TaskRecord,
)
from app.domain.devboard.paths import overlaps
from app.domain.devboard.publisher import DevRunSummary, parse_dev_run_outcome
from app.domain.devboard.runs import RunService
from app.domain.errors import Forbidden, ValidationFailed
from app.domain.ports import Clock
from app.domain.ports.devboard import LeaseOutcome, RunStore, TaskStore, TextRedactor

type Sleep = Callable[[float], Awaitable[None]]

POLL_SECONDS = 1.0
# Runs that only read the repo (the sandboxed triager, dev-lead planner and reviewer): no
# leases, ever.
READ_ONLY_RUN_KINDS = frozenset({RunKind.TRIAGE, RunKind.PLAN, RunKind.REVIEW})
# How far back a review run looks for the dev run that published the card's branch.
DEV_RUN_LOOKBACK = 50
# Triage runs see the other open cards (for the duplicate check), at most this many.
MAX_OPEN_CARDS = 50
OPEN_CARD_COLUMNS = tuple(column for column in Column if column is not Column.DONE)


class TaskOwners(Protocol):
    async def owner_of(self, task_id: uuid.UUID) -> uuid.UUID | None:
        """The account that created the (live) task, or None."""
        ...


@dataclass(frozen=True)
class Scope:
    actor: Actor
    task: TaskRecord
    run: RunRecord


@dataclass(frozen=True)
class Stores:
    owners: TaskOwners
    tasks: TaskStore
    runs: RunStore


@dataclass(frozen=True)
class Services:
    board: BoardService
    runs: RunService
    leases: LeaseService
    redactor: TextRedactor


class CoordinatorService:
    def __init__(
        self,
        stores: Stores,
        services: Services,
        clock: Clock,
        sleep: Sleep,
        *,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._stores = stores
        self._services = services
        self._clock = clock
        self._sleep = sleep
        self._poll = poll_seconds

    # ------------------------------------------------------------ authorisation
    async def authorize(self, grant: Grant) -> Scope:
        """The token's task and run, or `Forbidden` (never says which check failed)."""
        owner = await self._stores.owners.owner_of(grant.task_id)
        if owner is None:
            raise Forbidden("This token can't be used.")
        actor = Actor(user_id=owner, kind=ActorKind.AGENT)
        task = await self._stores.tasks.get(owner, grant.task_id)
        if task is None:
            raise Forbidden("This token can't be used.")
        run = await self._active_run(actor, grant.task_id, grant.run_id)
        return Scope(actor=actor, task=task, run=run)

    async def _active_run(self, actor: Actor, task_id: uuid.UUID, run_id: uuid.UUID) -> RunRecord:
        active = await self._stores.runs.active(actor.user_id, task_id)
        run = next((run for run in active if run.id == run_id and run.task_id == task_id), None)
        if run is None:
            raise Forbidden("This token can't be used.")
        return run

    # ------------------------------------------------------------ get_task
    async def get_task(self, scope: Scope, cursor: int) -> dict[str, Json]:
        owner = scope.actor.user_id
        plan = await self._stores.tasks.latest_plan(owner, scope.task.id)
        if scope.run.kind is RunKind.REVIEW:
            return await self._review_page(scope, plan, cursor)
        triage = await self._stores.tasks.latest_triage(owner, scope.task.id)
        held = [
            lease
            for lease in await self._services.leases.active(scope.actor)
            if lease.run_id == scope.run.id
        ]
        cards = await self._open_cards(scope) if scope.run.kind is RunKind.TRIAGE else []
        header = pages.header(scope.task, scope.run, plan, triage)
        items = pages.items(scope.task, scope.run, (triage, plan), held, cards)
        if scope.run.kind is not RunKind.DEV:
            return pages.page(header, items, cursor)
        items = pages.with_blockers(items, "review_blockers", await self._rework_blockers(scope))
        return pages.page(header, items, cursor, pages.DEV_LISTS)

    async def _review_page(
        self, scope: Scope, plan: PlanRecord | None, cursor: int
    ) -> dict[str, Json]:
        latest = await self._stores.tasks.latest_review(scope.actor.user_id, scope.task.id)
        previous = () if latest is None else latest.blockers
        header = pages.review_header(scope.task, scope.run, await self._dev_summary(scope))
        items = pages.review_items(scope.task, plan, previous)
        return pages.page(header, items, cursor, pages.REVIEW_LISTS)

    async def _dev_summary(self, scope: Scope) -> DevRunSummary | None:
        """The counts of the succeeded dev run that published the card's branch."""
        branch = scope.task.branch
        if branch is None:
            return None
        runs = await self._stores.runs.for_task(
            scope.actor.user_id, scope.task.id, 0, DEV_RUN_LOOKBACK
        )
        for run in runs:
            if run.kind is not RunKind.DEV or run.status is not RunStatus.SUCCEEDED:
                continue
            summary = parse_dev_run_outcome(run.outcome)
            if summary is not None and summary.branch == branch:
                return summary
        return None

    async def _rework_blockers(self, scope: Scope) -> tuple[ReviewFinding, ...]:
        """The latest review's blockers, when the card has a branch to rework."""
        if scope.task.branch is None:
            return ()
        latest = await self._stores.tasks.latest_review(scope.actor.user_id, scope.task.id)
        if latest is None or latest.verdict is ReviewVerdict.APPROVED:
            return ()
        return latest.blockers

    async def _open_cards(self, scope: Scope) -> list[TaskRecord]:
        """Other live cards not yet done, in board order (one extra to drop the run's own)."""
        listed = await self._stores.tasks.list(
            scope.actor.user_id, OPEN_CARD_COLUMNS, 0, MAX_OPEN_CARDS + 1
        )
        return [card for card in listed if card.id != scope.task.id][:MAX_OPEN_CARDS]

    # ------------------------------------------------------------ leases
    async def claim_paths(
        self, scope: Scope, paths: tuple[str, ...], wait_s: int
    ) -> dict[str, Json]:
        if scope.run.kind in READ_ONLY_RUN_KINDS:
            raise Forbidden("This run is read-only; it can't claim leases.")
        targets = lease_targets(paths)  # refuses bad paths before anything is queued
        started = self._clock.now()
        deadline = started + timedelta(seconds=wait_s)
        leases = self._services.leases
        while True:
            outcome = await leases.claim(scope.actor, scope.run.id, scope.task.id, paths)
            if outcome.granted:
                return _claimed(outcome, waited=self._clock.now() - started)
            if self._clock.now() >= deadline:
                return _refused(outcome, waited=self._clock.now() - started)
            await self._wait_for_clear(scope, targets, deadline)

    async def _wait_for_clear(
        self, scope: Scope, targets: tuple[tuple[str, LeaseKind], ...], deadline: datetime
    ) -> None:
        """Sleep until no other run holds an overlapping lease, or until the deadline. Reads
        only (a claim attempt queues a wait row, so it isn't repeated every poll)."""
        while (remaining := (deadline - self._clock.now()).total_seconds()) > 0:
            await self._sleep(min(self._poll, remaining))
            await self._active_run(scope.actor, scope.task.id, scope.run.id)
            live = await self._services.leases.active(scope.actor)
            blocked = any(
                lease.run_id != scope.run.id and overlaps(lease.path, path)
                for lease in live
                for path, _ in targets
            )
            if not blocked:
                return

    async def renew_lease(self, scope: Scope) -> dict[str, Json]:
        renewed = await self._services.leases.heartbeat(scope.actor, scope.run.id)
        held: list[Json] = [
            pages.lease_json(lease)
            for lease in await self._services.leases.active(scope.actor)
            if lease.run_id == scope.run.id
        ]
        return {"renewed": renewed, "leases": held}

    async def release_paths(
        self, scope: Scope, paths: tuple[str, ...] | None = None
    ) -> dict[str, Json]:
        """Release this run's leases. With `paths`, they must cover every lease the run holds:
        `LeaseStore.release` releases by run or task only, so a partial release is refused."""
        if paths is not None:
            requested = {path for path, _ in lease_targets(paths)}
            held = {
                lease.path
                for lease in await self._services.leases.active(scope.actor)
                if lease.run_id == scope.run.id
            }
            if held - requested:
                raise ValidationFailed(
                    "Releasing only some of the run's leases isn't supported yet; "
                    "omit `paths` to release them all."
                )
        return {"released": await self._services.leases.release_run(scope.actor, scope.run.id)}

    # ------------------------------------------------------------ messages
    async def post_step(self, scope: Scope, kind: StepKind, text: str) -> dict[str, Json]:
        step = await self._services.runs.append(
            scope.actor, scope.run.id, NewStep(kind=kind, summary=text, agent=scope.run.agent)
        )
        return {"seq": step.seq}

    async def post_handoff(self, scope: Scope, raw: str | bytes) -> dict[str, Json]:
        accepted = parse_capped(AgentHandoff, raw)
        step = await self._services.runs.append(
            scope.actor,
            scope.run.id,
            NewStep(
                kind=StepKind.HANDOFF, summary=accepted.model_dump_json(), agent=scope.run.agent
            ),
        )
        return {"accepted": True, "seq": step.seq, "status": accepted.status}

    async def request_help(self, scope: Scope, text: str) -> dict[str, Json]:
        # Refused (never cut) over the request_help cap; the run id counts towards it.
        parse_capped(RequestHelp, json.dumps({"run_id": str(scope.run.id), "question": text}))
        reason = await self._services.redactor.redact(text)
        step = await self._services.runs.append(
            scope.actor,
            scope.run.id,
            NewStep(
                kind=StepKind.MESSAGE, summary=f"Help requested: {text}", agent=scope.run.agent
            ),
        )
        task = await self._services.board.get(scope.actor, scope.task.id)
        if task.column is not Column.BLOCKED:
            task = await self._services.board.system_move(
                scope.actor, task.id, Column.BLOCKED, reason=reason, run_id=scope.run.id
            )
        return {"column": task.column.value, "seq": step.seq}


def _claimed(outcome: LeaseOutcome, waited: timedelta) -> dict[str, Json]:
    return {
        "granted": [pages.lease_json(lease) for lease in outcome.granted],
        "waiting": [],
        "waited_s": math.ceil(waited.total_seconds()),
    }


def _refused(outcome: LeaseOutcome, waited: timedelta) -> dict[str, Json]:
    """Timed out: `waiting` lists the conflicting leased paths, never another task's or run's
    id."""
    paths = sorted({lease.path for lease in outcome.conflicts})
    waiting: list[Json] = [*paths]
    return {"granted": [], "waiting": waiting, "waited_s": math.ceil(waited.total_seconds())}
