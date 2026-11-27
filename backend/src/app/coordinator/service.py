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

from app.coordinator.schemas import RESULT_TOKENS, AgentHandoff, parse_capped
from app.coordinator.tokens import Grant
from app.domain.devboard.board import BoardService
from app.domain.devboard.leases import LeaseService, lease_targets
from app.domain.devboard.messages import RequestHelp, estimate_tokens
from app.domain.devboard.model import (
    Actor,
    ActorKind,
    Column,
    Criterion,
    LeaseKind,
    LeaseRecord,
    NewStep,
    PlanRecord,
    RunRecord,
    StepKind,
    Subtask,
    TaskRecord,
    TriageRecord,
)
from app.domain.devboard.paths import overlaps
from app.domain.devboard.runs import RunService
from app.domain.errors import Forbidden, ValidationFailed
from app.domain.ports import Clock
from app.domain.ports.devboard import LeaseOutcome, RunStore, TaskStore, TextRedactor

type Json = str | int | float | bool | list[Json] | dict[str, Json] | None
type Sleep = Callable[[float], Awaitable[None]]

POLL_SECONDS = 1.0
BRIEF_CHUNK_CHARS = 1500
# Room left under the result cap for redaction markers that are longer than what they replace.
_PAGE_TOKENS = RESULT_TOKENS - 150


class TaskOwners(Protocol):
    async def owner_of(self, task_id: uuid.UUID) -> uuid.UUID | None:
        """The account that created the (live) task, or None."""
        ...


class ResultTooLarge(ValidationFailed):
    code = "result-too-large"


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


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _lease(lease: LeaseRecord) -> Json:
    return {"path": lease.path, "kind": lease.kind.value, "expires_at": _iso(lease.expires_at)}


def _chunks(text: str, size: int = BRIEF_CHUNK_CHARS) -> list[str]:
    """Split on whitespace near `size` so pages never break a word unless it is huge."""
    pieces: list[str] = []
    rest = text
    while len(rest) > size:
        cut = rest.rfind(" ", size // 2, size)
        cut = size if cut <= 0 else cut + 1
        pieces.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        pieces.append(rest)
    return pieces


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
        header = _header(scope, plan)
        items = _items(scope, triage, plan, held)
        if cursor > len(items):
            raise ValidationFailed("cursor is past the last page.")
        return _page(header, items, cursor)

    # ------------------------------------------------------------ leases
    async def claim_paths(
        self, scope: Scope, paths: tuple[str, ...], wait_s: int
    ) -> dict[str, Json]:
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
            _lease(lease)
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


# ------------------------------------------------------------------ get_task pages
def _header(scope: Scope, plan: PlanRecord | None) -> dict[str, Json]:
    task, run = scope.task, scope.run
    budget_usd = run.budget_usd
    if budget_usd is None and plan is not None:
        budget_usd = plan.budget_usd
    return {
        "task": {
            "id": str(task.id),
            "title": task.title,
            "column": task.column.value,
            "type": None if task.type is None else task.type.value,
            "size": None if task.size is None else task.size.value,
            "risk": None if task.risk is None else task.risk.value,
            "areas": [area.value for area in task.areas],
        },
        "run": {
            "id": str(run.id),
            "kind": run.kind.value,
            "agent": run.agent,
            "status": run.status.value,
            "round": run.round,
        },
        "budget": {
            "usd": budget_usd,
            "minutes": None if plan is None else plan.budget_minutes,
        },
        "plan": None
        if plan is None
        else {"mode": plan.mode, "version": plan.version, "approved": plan.is_approved},
    }


_LISTS = ("acceptance_criteria", "acceptance_tests", "subtasks", "lease_paths", "leases_held")


def _items(
    scope: Scope,
    triage: TriageRecord | None,
    plan: PlanRecord | None,
    held: list[LeaseRecord],
) -> list[tuple[str, Json]]:
    items: list[tuple[str, Json]] = [("brief", chunk) for chunk in _chunks(scope.task.body)]
    if triage is not None:
        items += [("acceptance_criteria", _criterion(c)) for c in triage.criteria]
    if plan is not None:
        items += [("acceptance_tests", test) for test in plan.acceptance_tests]
        items += [("subtasks", _subtask(s)) for s in plan.subtasks]
        items += [("lease_paths", path) for path in _lease_paths(scope.run, plan)]
    items += [("leases_held", _lease(lease)) for lease in held]
    return items


def _criterion(criterion: Criterion) -> Json:
    return {"test_id": criterion.test_id, "expectation": criterion.expectation}


def _subtask(subtask: Subtask) -> Json:
    return {
        "id": subtask.id,
        "title": subtask.title,
        "agent": subtask.agent,
        "lease_paths": [*subtask.lease_paths],
        "depends_on": [*subtask.depends_on],
        "is_test_only": subtask.is_test_only,
    }


def _lease_paths(run: RunRecord, plan: PlanRecord) -> list[str]:
    """The run's own subtask lease (matched by agent), else every subtask's (the lead)."""
    mine = [s for s in plan.subtasks if run.agent is not None and s.agent == run.agent]
    chosen = mine or list(plan.subtasks)
    return list(dict.fromkeys(path for s in chosen for path in s.lease_paths))


def _empty_page(header: dict[str, Json]) -> dict[str, Json]:
    page: dict[str, Json] = dict(header)
    page["brief"] = ""
    for name in _LISTS:
        page[name] = []
    page["next_cursor"] = None
    return page


def _add(page: dict[str, Json], name: str, value: Json) -> None:
    if name == "brief":
        current = page["brief"]
        assert isinstance(current, str)
        page["brief"] = current + str(value)
        return
    bucket = page[name]
    assert isinstance(bucket, list)
    bucket.append(value)


def _tokens(page: dict[str, Json]) -> int:
    return estimate_tokens(json.dumps(page, ensure_ascii=False))


def _page(header: dict[str, Json], items: list[tuple[str, Json]], cursor: int) -> dict[str, Json]:
    """Greedy pages under the result cap; `next_cursor` is the first item not on this page."""
    page = _empty_page(header)
    index = cursor
    while index < len(items):
        name, value = items[index]
        trial = json.loads(json.dumps(page))
        _add(trial, name, value)
        if _tokens(trial) > _PAGE_TOKENS:
            if index == cursor:
                raise ResultTooLarge("A task item is too large for one result page.")
            break
        page = trial
        index += 1
    page["brief_complete"] = not any(name == "brief" for name, _ in items[index:])
    page["next_cursor"] = index if index < len(items) else None
    page["total_items"] = len(items)
    return page


def _claimed(outcome: LeaseOutcome, waited: timedelta) -> dict[str, Json]:
    return {
        "granted": [_lease(lease) for lease in outcome.granted],
        "waiting": [],
        "waited_s": math.ceil(waited.total_seconds()),
    }


def _refused(outcome: LeaseOutcome, waited: timedelta) -> dict[str, Json]:
    """Timed out: `waiting` lists the conflicting leased paths, never another task's or run's
    id."""
    paths = sorted({lease.path for lease in outcome.conflicts})
    waiting: list[Json] = [*paths]
    return {"granted": [], "waiting": waiting, "waited_s": math.ceil(waited.total_seconds())}
