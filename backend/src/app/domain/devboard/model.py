"""Dev-board value objects. They mirror the spec enums; `app.api.devboard` maps them to the
generated models (the domain never imports `app.generated`, ARCH-01)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain.identity import AppRole, Principal


class Column(StrEnum):
    BACKLOG = "backlog"
    TRIAGED = "triaged"
    OUT_OF_SCOPE = "out_of_scope"
    PLAN_REVIEW = "plan_review"
    READY = "ready"
    AGENT_DEV = "agent_dev"
    AGENT_REVIEW = "agent_review"
    CHANGES_REQUESTED = "changes_requested"
    HUMAN_REVIEW = "human_review"
    BLOCKED = "blocked"
    DONE = "done"


COLUMN_ORDER: tuple[Column, ...] = tuple(Column)


class TaskType(StrEnum):
    FEATURE = "feature"
    BUG = "bug"
    SECURITY = "security"
    PERFORMANCE = "performance"
    CHORE = "chore"
    REFACTOR = "refactor"
    TEST = "test"
    DOCS = "docs"


class Priority(StrEnum):
    """P0 drop everything, P1 this milestone, P2 soon, P3 whenever."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class Severity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    TRIVIAL = "trivial"


class Route(StrEnum):
    """`now` starts planning; `next` waits at the top of Triaged; `later` at the bottom."""

    NOW = "now"
    NEXT = "next"
    LATER = "later"


class VerificationStatus(StrEnum):
    CONFIRMED = "confirmed"
    NOT_REPRODUCED = "not_reproduced"
    UNCLEAR = "unclear"
    DUPLICATE = "duplicate"


class ReviewSeverity(StrEnum):
    BLOCKER = "BLOCKER"
    NIT = "NIT"


class ReviewVerdict(StrEnum):
    """`approved` -> human_review; `changes_requested` -> rework; `needs_decision` -> human."""

    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    NEEDS_DECISION = "needs_decision"


class Size(StrEnum):
    S = "S"
    M = "M"
    L = "L"


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


RISK_ORDER: tuple[Risk, ...] = (Risk.LOW, Risk.MEDIUM, Risk.HIGH)


def max_risk(*risks: Risk) -> Risk:
    return max(risks, key=RISK_ORDER.index, default=Risk.LOW)


class Area(StrEnum):
    WEB = "web"
    API = "api"


class ActorKind(StrEnum):
    MAINTAINER = "maintainer"
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class EventKind(StrEnum):
    CREATED = "created"
    EDITED = "edited"
    MOVED = "moved"
    TRIAGED = "triaged"
    PLANNED = "planned"
    APPROVED = "approved"
    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    KILLED = "killed"
    PII_REDACTED = "pii_redacted"


class RunKind(StrEnum):
    TRIAGE = "triage"
    PLAN = "plan"
    TEST_WRITER = "test_writer"
    DEV = "dev"
    PUBLISH = "publish"
    REVIEW = "review"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    KILLED = "killed"
    TIMED_OUT = "timed_out"


TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.KILLED, RunStatus.TIMED_OUT}
)


class StepKind(StrEnum):
    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    MESSAGE = "message"
    HANDOFF = "handoff"
    LEASE = "lease"
    STATUS = "status"
    FINDING = "finding"


class LeaseKind(StrEnum):
    EXCLUSIVE = "exclusive"
    HOTSPOT = "hotspot"


@dataclass(frozen=True)
class Actor:
    """Who is acting: a signed-in account, or the system/agents on that account's behalf.
    `user_id` scopes row-level security for every board write."""

    user_id: uuid.UUID
    kind: ActorKind
    role: AppRole | None = None

    @staticmethod
    def of(principal: Principal) -> Actor:
        kind = ActorKind.MAINTAINER if principal.is_maintainer else ActorKind.USER
        return Actor(user_id=principal.user_id, kind=kind, role=principal.role)

    @property
    def is_maintainer(self) -> bool:
        return self.role is AppRole.MAINTAINER

    def as_system(self) -> Actor:
        return Actor(user_id=self.user_id, kind=ActorKind.SYSTEM, role=self.role)

    def as_agent(self) -> Actor:
        return Actor(user_id=self.user_id, kind=ActorKind.AGENT, role=self.role)


@dataclass(frozen=True)
class TaskRecord:
    id: uuid.UUID
    title: str
    body: str
    column: Column
    rank: str
    version: int
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    created_by_role: AppRole | None = None
    type: TaskType | None = None
    size: Size | None = None
    risk: Risk | None = None
    areas: tuple[Area, ...] = ()
    labels: tuple[str, ...] = ()
    needs_human_design: bool = False
    priority: Priority | None = None
    severity: Severity | None = None
    route: Route | None = None
    out_of_scope_reason: str | None = None
    blocked_reason: str | None = None
    blocked_from: Column | None = None
    pii_redacted: bool = False
    pr_url: str | None = None
    branch: str | None = None
    base_sha: str | None = None
    # The GitHub branch of the card's current attempt (CODE_HOST=github); PR sync follows it.
    pr_branch: str | None = None
    review_round: int = 0
    active_run_id: uuid.UUID | None = None


@dataclass(frozen=True)
class TaskEventRecord:
    id: uuid.UUID
    task_id: uuid.UUID
    at: datetime
    kind: EventKind
    actor_kind: ActorKind
    actor_user_id: uuid.UUID | None = None
    from_column: Column | None = None
    to_column: Column | None = None
    run_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Criterion:
    test_id: str
    expectation: str


@dataclass(frozen=True)
class ReviewFinding:
    """A reviewer finding after code's rules: an uncited BLOCKER is a NIT (`downgraded`)."""

    severity: ReviewSeverity
    path: str
    evidence: str
    rubric_id: str | None = None
    test_id: str | None = None
    repro: str | None = None
    line: int | None = None
    fix: str | None = None
    downgraded: bool = False


@dataclass(frozen=True)
class ReviewRecord:
    id: uuid.UUID
    task_id: uuid.UUID
    run_id: uuid.UUID
    round: int
    verdict: ReviewVerdict
    summary: str
    findings: tuple[ReviewFinding, ...]
    created_at: datetime
    model: str | None = None

    @property
    def blockers(self) -> tuple[ReviewFinding, ...]:
        return tuple(f for f in self.findings if f.severity is ReviewSeverity.BLOCKER)


@dataclass(frozen=True)
class Verification:
    """How triage checked the issue: against the repo (read-only) and the open cards.
    Every `code_refs` path exists in the repo; `duplicate_of` is set only for DUPLICATE."""

    status: VerificationStatus
    evidence: str
    code_refs: tuple[str, ...] = ()
    duplicate_of: uuid.UUID | None = None


@dataclass(frozen=True)
class TriageRecord:
    task_id: uuid.UUID
    type: TaskType
    size: Size
    risk: Risk
    areas: tuple[Area, ...]
    priority: Priority
    severity: Severity
    impact: str
    route: Route
    route_reason: str
    verification: Verification
    needs_human_design: bool
    in_scope: bool
    created_at: datetime
    out_of_scope_reason: str | None = None
    model: str | None = None
    provider: str | None = None
    # The model's own paths (normalised, checked against the repo); a starting point for plans.
    likely_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class Subtask:
    id: str
    title: str
    agent: str
    lease_paths: tuple[str, ...]
    depends_on: tuple[str, ...] = ()
    is_test_only: bool = False


@dataclass(frozen=True)
class PlanRecord:
    id: uuid.UUID
    task_id: uuid.UUID
    version: int
    mode: str
    subtasks: tuple[Subtask, ...]
    acceptance_tests: tuple[str, ...]
    budget_usd: float
    budget_minutes: int
    risk: Risk
    created_at: datetime
    tests_to_change: tuple[str, ...] = ()
    # Written by the planner against the repo; triage writes none.
    acceptance_criteria: tuple[Criterion, ...] = ()
    tests_approved_at: datetime | None = None
    plan_approved_at: datetime | None = None
    approved_by: uuid.UUID | None = None

    @property
    def is_approved(self) -> bool:
        return self.tests_approved_at is not None and self.plan_approved_at is not None


@dataclass(frozen=True)
class RunRecord:
    id: uuid.UUID
    task_id: uuid.UUID
    kind: RunKind
    status: RunStatus
    created_at: datetime
    parent_run_id: uuid.UUID | None = None
    agent: str | None = None
    round: int = 0
    model: str | None = None
    provider: str | None = None
    outcome: str | None = None
    budget_usd: float | None = None
    cost_usd: float = 0.0
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def is_finished(self) -> bool:
        return self.status in TERMINAL_RUN_STATUSES


@dataclass(frozen=True)
class StepRecord:
    id: uuid.UUID
    run_id: uuid.UUID
    seq: int
    kind: StepKind
    summary: str
    at: datetime
    agent: str | None = None
    tool: str | None = None


@dataclass(frozen=True)
class NewStep:
    """A step before it is redacted, numbered and stored."""

    kind: StepKind
    summary: str
    agent: str | None = None
    tool: str | None = None


@dataclass(frozen=True)
class LeaseRecord:
    id: uuid.UUID
    run_id: uuid.UUID
    task_id: uuid.UUID
    path: str
    kind: LeaseKind
    granted_at: datetime
    expires_at: datetime
    heartbeat_at: datetime | None = None
    released_at: datetime | None = None

    def is_active(self, now: datetime) -> bool:
        return self.released_at is None and self.expires_at > now


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    next_cursor: str | None = None


@dataclass(frozen=True)
class TaskQuery:
    columns: tuple[Column, ...] = ()
    limit: int = 50
    cursor: str | None = None


@dataclass(frozen=True)
class TaskEdit:
    version: int
    title: str | None = None
    body: str | None = None
    labels: tuple[str, ...] | None = None


@dataclass(frozen=True)
class MoveRequest:
    to_column: Column
    rank: str
    version: int
    reason: str | None = None
