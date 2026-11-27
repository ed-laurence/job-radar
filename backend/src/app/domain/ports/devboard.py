"""Dev-board ports. Postgres adapters live in `app.adapters.db.devboard_{tasks,runs,leases}`, the
in-memory fakes in `app.adapters.fakes.devboard`. Every call takes the acting `user_id`, which
scopes row-level security for the shared board tables (SEC-03)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.devboard.model import (
    Actor,
    Area,
    Column,
    Criterion,
    LeaseKind,
    LeaseRecord,
    NewStep,
    PlanRecord,
    Risk,
    RunRecord,
    RunStatus,
    Size,
    StepRecord,
    Subtask,
    TaskEventRecord,
    TaskRecord,
    TaskType,
    TriageRecord,
)


class TaskStore(Protocol):
    async def insert(self, user_id: uuid.UUID, task: TaskRecord) -> None: ...

    async def get(self, user_id: uuid.UUID, task_id: uuid.UUID) -> TaskRecord | None:
        """Soft-deleted cards are not returned."""
        ...

    async def list(
        self, user_id: uuid.UUID, columns: tuple[Column, ...], offset: int, limit: int
    ) -> list[TaskRecord]:
        """Live cards in board order (column, rank, id); every column when `columns` is empty."""
        ...

    async def save(self, user_id: uuid.UUID, task: TaskRecord, expected_version: int) -> TaskRecord:
        """Compare-and-set on `version`: stores `task` with version + 1, or raises `Conflict`
        (with `current_version`) / `NotFound`."""
        ...

    async def delete(
        self, user_id: uuid.UUID, task_id: uuid.UUID, expected_version: int, at: datetime
    ) -> None:
        """Soft delete with the same compare-and-set as `save`."""
        ...

    async def column_ranks(self, user_id: uuid.UUID, column: Column) -> list[tuple[str, uuid.UUID]]:
        """`(rank, id)` of live cards in `column`, in board order."""
        ...

    async def set_ranks(self, user_id: uuid.UUID, ranks: dict[uuid.UUID, str]) -> None:
        """Rebalance: new ranks for whole column (bumps each card's version)."""
        ...

    async def add_event(self, user_id: uuid.UUID, event: TaskEventRecord) -> None: ...

    async def events(
        self, user_id: uuid.UUID, task_id: uuid.UUID, offset: int, limit: int
    ) -> list[TaskEventRecord]:
        """Newest first."""
        ...

    async def add_triage(self, user_id: uuid.UUID, triage: TriageRecord) -> None: ...

    async def latest_triage(
        self, user_id: uuid.UUID, task_id: uuid.UUID
    ) -> TriageRecord | None: ...

    async def add_plan(self, user_id: uuid.UUID, plan: PlanRecord) -> None: ...

    async def latest_plan(self, user_id: uuid.UUID, task_id: uuid.UUID) -> PlanRecord | None: ...

    async def save_plan(
        self, user_id: uuid.UUID, plan: PlanRecord, expected_version: int
    ) -> PlanRecord:
        """Compare-and-set like `save`."""
        ...


@dataclass(frozen=True)
class RunFinish:
    status: RunStatus
    outcome: str
    at: datetime
    cost_usd: float = 0.0
    model: str | None = None
    provider: str | None = None


class RunStore(Protocol):
    async def insert(self, user_id: uuid.UUID, run: RunRecord) -> None:
        """Raises `Conflict` when the task already has an active top-level run."""
        ...

    async def get(self, user_id: uuid.UUID, run_id: uuid.UUID) -> RunRecord | None: ...

    async def for_task(
        self, user_id: uuid.UUID, task_id: uuid.UUID, offset: int, limit: int
    ) -> list[RunRecord]:
        """Newest first."""
        ...

    async def active(self, user_id: uuid.UUID, task_id: uuid.UUID) -> list[RunRecord]:
        """Queued or running runs of the task, top-level and children."""
        ...

    async def mark_running(self, user_id: uuid.UUID, run_id: uuid.UUID, at: datetime) -> None: ...

    async def append_step(
        self, user_id: uuid.UUID, run_id: uuid.UUID, step: NewStep, at: datetime
    ) -> StepRecord:
        """Numbers the step (`seq` = previous + 1). The insert notifies `run_steps`."""
        ...

    async def finish(
        self, user_id: uuid.UUID, run_id: uuid.UUID, finish: RunFinish, final: NewStep
    ) -> RunRecord | None:
        """Atomically append the final step and set the terminal status. No-op (None) when
        the run already finished, so a kill and a natural finish can't both win."""
        ...

    async def steps_after(
        self, user_id: uuid.UUID, run_id: uuid.UUID, after_seq: int, limit: int
    ) -> list[StepRecord]: ...


@dataclass(frozen=True)
class LeaseClaim:
    run_id: uuid.UUID
    task_id: uuid.UUID
    paths: tuple[tuple[str, LeaseKind], ...]
    at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class LeaseOutcome:
    granted: tuple[LeaseRecord, ...]
    conflicts: tuple[LeaseRecord, ...]


class LeaseStore(Protocol):
    async def claim(self, user_id: uuid.UUID, claim: LeaseClaim) -> LeaseOutcome:
        """All or nothing, atomically: refuse (and queue a wait) when any requested path
        overlaps an active lease of another run; otherwise grant every path."""
        ...

    async def renew(
        self, user_id: uuid.UUID, run_id: uuid.UUID, at: datetime, expires_at: datetime
    ) -> int:
        """Heartbeat every still-active lease of the run. Returns how many were renewed."""
        ...

    async def release(
        self,
        user_id: uuid.UUID,
        at: datetime,
        *,
        run_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
    ) -> int: ...

    async def reclaim_expired(self, user_id: uuid.UUID, at: datetime) -> int:
        """Mark expired, unreleased leases released (the watchdog)."""
        ...

    async def active(self, user_id: uuid.UUID, at: datetime) -> list[LeaseRecord]: ...


class TextRedactor(Protocol):
    """`app.guards.redaction.Redactor`: secret patterns, denylist, PII, guard."""

    async def redact(self, text: str) -> str: ...


class Dispatcher(Protocol):
    """Runs background work (triage, plans, simulated runs) without blocking the request."""

    def spawn(self, key: uuid.UUID, work: Callable[[], Awaitable[None]]) -> None: ...

    def cancel(self, key: uuid.UUID) -> bool: ...


@dataclass(frozen=True)
class RunContext:
    """What an agent run gets: never the conversation, only the card and its plan."""

    run: RunRecord
    task: TaskRecord
    actor: Actor
    plan: PlanRecord | None
    triage: TriageRecord | None


class RunTools(Protocol):
    """What the coordinator gives a running agent: steps, leases and the kill check."""

    async def emit(self, step: NewStep) -> StepRecord: ...

    async def claim_paths(self, paths: tuple[str, ...]) -> LeaseOutcome: ...

    async def renew_leases(self) -> int: ...

    async def release_paths(self) -> int: ...

    async def is_cancelled(self) -> bool: ...


@dataclass(frozen=True)
class TriageDecision:
    """The triage agent's answer before code applies the risk map and the scope check."""

    type: TaskType
    size: Size
    llm_risk: Risk
    areas: tuple[Area, ...]
    criteria: tuple[Criterion, ...]
    needs_human_design: bool
    likely_paths: tuple[str, ...]
    out_of_scope_reason: str | None = None
    model: str | None = None
    provider: str | None = None
    cost_usd: float = 0.0


class TriageAgent(Protocol):
    async def triage(self, context: RunContext, tools: RunTools) -> TriageDecision:
        """Raises a `DomainError` (guard, budget, validation, upstream) when it can't decide."""
        ...


@dataclass(frozen=True)
class PlanDraft:
    mode: str
    subtasks: tuple[Subtask, ...]
    acceptance_tests: tuple[str, ...]
    budget_usd: float
    budget_minutes: int
    llm_risk: Risk


@dataclass(frozen=True)
class AgentResult:
    is_success: bool
    outcome: str
    cost_usd: float = 0.0
    model: str | None = None
    provider: str | None = None
    plan: PlanDraft | None = None


class AgentRunner(Protocol):
    """Runs the dev-lead (plan) and dev agents. M1 ships a simulation; the sandboxed runner
    arrives in M2 behind the same port."""

    async def run(self, context: RunContext, tools: RunTools) -> AgentResult: ...
