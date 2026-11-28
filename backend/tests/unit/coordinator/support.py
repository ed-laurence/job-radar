"""A coordinator runtime over the in-memory board stores, a fake clock and a fake sleep.

The fake sleep advances the clock and runs `on_sleep` hooks, so a test can release another
run's lease while `claim_paths` waits, without real time passing (TEST-04).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta

import httpx

from app.adapters.fakes.data import InMemoryNotificationHub
from app.adapters.fakes.devboard import InMemoryLeaseStore, InMemoryRunStore, InMemoryTaskStore
from app.adapters.fakes.security import FakeContentGuard, FakePII
from app.adapters.fakes.system import FakeClock, SequentialIds
from app.coordinator.service import CoordinatorService, Services, Stores, TaskOwners
from app.coordinator.tokens import mint
from app.coordinator.wiring import CoordinatorRuntime
from app.domain.devboard.board import BoardService
from app.domain.devboard.leases import LeaseService
from app.domain.devboard.model import (
    Area,
    Column,
    Criterion,
    PlanRecord,
    Priority,
    Risk,
    Route,
    RunKind,
    RunRecord,
    RunStatus,
    Severity,
    Size,
    Subtask,
    TaskRecord,
    TaskType,
    TriageRecord,
    Verification,
    VerificationStatus,
)
from app.domain.devboard.runs import RunService
from app.guards.redaction import Redactor

KEY = b"coordinator-test-key-0123456789abcdef"
OWNER = uuid.UUID("00000000-0000-4000-8000-00000000f001")
OTHER_OWNER = uuid.UUID("00000000-0000-4000-8000-00000000f002")
DENYLISTED = "proj-xxxx-internal"
BASE_URL = "http://coordinator:8100"
HOSTS = ("coordinator:8100",)


class MemoryOwners:
    def __init__(self, tasks: InMemoryTaskStore) -> None:
        self._tasks = tasks

    async def owner_of(self, task_id: uuid.UUID) -> uuid.UUID | None:
        task = await self._tasks.get(OWNER, task_id)
        return None if task is None else task.created_by


@dataclass
class Harness:
    runtime: CoordinatorRuntime
    tasks: InMemoryTaskStore
    runs: InMemoryRunStore
    leases: InMemoryLeaseStore
    lease_service: LeaseService
    clock: FakeClock
    ids: SequentialIds
    slept: list[float] = field(default_factory=list[float])
    on_sleep: list[Callable[[], Awaitable[None]]] = field(
        default_factory=list[Callable[[], Awaitable[None]]]
    )

    @property
    def service(self) -> CoordinatorService:
        return self.runtime.service

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.clock.advance(timedelta(seconds=seconds))
        for hook in list(self.on_sleep):
            await hook()

    async def add_task(
        self,
        *,
        owner: uuid.UUID = OWNER,
        body: str = "Add an empty state to the contacts list.",
        column: Column = Column.AGENT_DEV,
        with_plan: bool = True,
        with_triage: bool = True,
        labels: tuple[str, ...] = (),
        tests_to_change: tuple[str, ...] = (),
    ) -> TaskRecord:
        now = self.clock.now()
        task = TaskRecord(
            id=self.ids.new_id(),
            title="Contacts empty state",
            body=body,
            column=column,
            rank="U",
            version=1,
            created_by=owner,
            created_at=now,
            updated_at=now,
            type=TaskType.FEATURE,
            size=Size.S,
            risk=Risk.LOW,
            areas=(Area.WEB,),
            labels=labels,
        )
        await self.tasks.insert(owner, task)
        criteria = (Criterion("web/src/features/contacts/empty.test.tsx::shows", "Shows it."),)
        triage = TriageRecord(
            task_id=task.id,
            type=TaskType.FEATURE,
            size=Size.S,
            risk=Risk.LOW,
            areas=(Area.WEB,),
            priority=Priority.P2,
            severity=Severity.MINOR,
            impact="Users with no contacts see a blank list.",
            route=Route.NOW,
            route_reason="Small, self-contained web change.",
            verification=Verification(
                status=VerificationStatus.CONFIRMED,
                evidence="The contacts list renders nothing when empty.",
                code_refs=("web/src/features/",),
            ),
            likely_paths=("web/src/features/contacts/ContactList.tsx",),
            needs_human_design=False,
            in_scope=True,
            created_at=now,
        )
        if with_triage:
            await self.tasks.add_triage(owner, triage)
        if with_plan:
            plan = PlanRecord(
                tests_to_change=tests_to_change,
                id=self.ids.new_id(),
                task_id=task.id,
                version=1,
                mode="single",
                subtasks=(
                    Subtask("s1", "Empty state", "frontend-engineer", ("web/src/features/",)),
                    Subtask("s2", "Route", "backend-engineer", ("backend/src/app/api/",)),
                ),
                acceptance_tests=("web/src/features/contacts/empty.test.tsx::shows",),
                budget_usd=2.5,
                budget_minutes=30,
                risk=Risk.LOW,
                created_at=now,
                acceptance_criteria=criteria,
                tests_approved_at=now,
                plan_approved_at=now,
            )
            await self.tasks.add_plan(owner, plan)
        return task

    async def add_run(
        self,
        task: TaskRecord,
        *,
        agent: str = "frontend-engineer",
        status: RunStatus = RunStatus.RUNNING,
        parent: uuid.UUID | None = None,
        kind: RunKind = RunKind.DEV,
    ) -> RunRecord:
        run = RunRecord(
            id=self.ids.new_id(),
            task_id=task.id,
            kind=kind,
            status=status,
            created_at=self.clock.now(),
            parent_run_id=parent,
            agent=agent,
            budget_usd=1.0,
        )
        await self.runs.insert(task.created_by, run)
        return run

    def token(self, task_id: uuid.UUID, run_id: uuid.UUID, ttl: int = 600) -> str:
        now = int(self.clock.now().timestamp())
        return mint(KEY, task_id=task_id, run_id=run_id, ttl_s=ttl, now=now)


def harness(owners: Callable[[InMemoryTaskStore], TaskOwners] = MemoryOwners) -> Harness:
    clock, ids = FakeClock(), SequentialIds()
    runs = InMemoryRunStore(ids, publisher=InMemoryNotificationHub())
    tasks = InMemoryTaskStore(runs)
    leases = InMemoryLeaseStore(ids)
    pii, guard = FakePII(), FakeContentGuard()
    redactor = Redactor((DENYLISTED,), pii, guard)
    lease_service = LeaseService(leases, clock)
    services = Services(
        board=BoardService(tasks, pii, clock, ids),
        runs=RunService(runs, tasks, InMemoryNotificationHub(), redactor, clock),
        leases=lease_service,
        redactor=redactor,
    )
    holder: list[Harness] = []

    async def sleep(seconds: float) -> None:
        await holder[0].sleep(seconds)

    service = CoordinatorService(
        Stores(owners=owners(tasks), tasks=tasks, runs=runs), services, clock, sleep
    )
    runtime = CoordinatorRuntime(
        service=service, redactor=redactor, key=KEY, clock=clock, allowed_hosts=HOSTS
    )
    built = Harness(runtime, tasks, runs, leases, lease_service, clock, ids)
    holder.append(built)
    return built


def headers(token: str | None) -> dict[str, str]:
    values = {"Accept": "application/json"}
    if token is not None:
        values["Authorization"] = f"Bearer {token}"
    return values


async def call(
    http: httpx.AsyncClient,
    token: str | None,
    method: str,
    path: str,
    body: Mapping[str, object] | None = None,
    *,
    content: bytes | None = None,
) -> httpx.Response:
    """One coordinator request. `content` sends raw bytes instead of `body` as JSON."""
    if content is None and body is not None:
        content = json.dumps(dict(body)).encode()
    values = headers(token)
    if content is not None:
        values["Content-Type"] = "application/json"
    return await http.request(method, path, content=content, headers=values)
