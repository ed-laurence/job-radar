"""The coordinator's tools over the dev-board domain services and the lease/run stores.

Every call starts from a `Scope` built from a verified token: the task, its owner (who scopes
row-level security, SEC-03) and the run, which must be one of the task's active runs. No tool
takes a task or run id, so none can reach another task.

Results are plain JSON values; `app.coordinator.server` redacts and caps them.
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
    RunKind,
    RunRecord,
    StepKind,
    TaskRecord,
)
from app.domain.devboard.paths import overlaps
from app.domain.devboard.runs import RunService
from app.domain.errors import Forbidden, ValidationFailed
from app.domain.ports import Clock
from app.domain.ports.devboard import LeaseOutcome, RunStore, TaskStore, TextRedactor

type Sleep = Callable[[float], Awaitable[None]]

POLL_SECONDS = 1.0
# Runs that only read the repo (the sandboxed triager and dev-lead planner): no leases, ever.
READ_ONLY_RUN_KINDS = frozenset({RunKind.TRIAGE, RunKind.PLAN})
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
        triage = await self._stores.tasks.latest_triage(owner, scope.task.id)
        plan = await self._stores.tasks.latest_plan(owner, scope.task.id)
        held = [
            lease
            for lease in await self._services.leases.active(scope.actor)
            if lease.run_id == scope.run.id
        ]
        cards = await self._open_cards(scope) if scope.run.kind is RunKind.TRIAGE else []
        header = pages.header(scope.task, scope.run, plan, triage)
        items = pages.items(scope.task, scope.run, (triage, plan), held, cards)
        if cursor > len(items):
            raise ValidationFailed("cursor is past the last page.")
        return pages.page(header, items, cursor)

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
