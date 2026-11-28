"""Dev-board tables (data-model.md "Dev board"). Shared: RLS only requires `app.user_id` set;
maintainer-only writes are enforced in the domain service."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Index, Numeric, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters.db import vocab
from app.adapters.db.base import (
    JSONB_COL,
    TEXT_ARRAY,
    Base,
    created,
    one_of,
    opt_ref,
    pk,
    rank,
    ref,
    required_stamp,
    stamp,
    subset_of,
    updated,
    version,
)

type JsonList = list[object]
type JsonDict = dict[str, object]


def _nullable_one_of(column: str, values: tuple[str, ...]) -> CheckConstraint:
    quoted = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f'"{column}" IS NULL OR "{column}" IN ({quoted})', name=column)


class DevTask(Base):
    __tablename__ = "dev_tasks"
    __table_args__ = (
        one_of("column", vocab.TASK_COLUMN),
        _nullable_one_of("blocked_from_column", vocab.TASK_COLUMN),
        _nullable_one_of("type", vocab.TASK_TYPE),
        _nullable_one_of("size", vocab.TASK_SIZE),
        _nullable_one_of("risk", vocab.TASK_RISK),
        _nullable_one_of("priority", vocab.TASK_PRIORITY),
        _nullable_one_of("severity", vocab.TASK_SEVERITY),
        _nullable_one_of("route", vocab.TRIAGE_ROUTE),
        subset_of("areas", vocab.TASK_AREA),
        Index("ix_dev_tasks_column_rank", "column", "rank"),
    )

    id: Mapped[uuid.UUID] = pk()
    title: Mapped[str]
    body: Mapped[str] = mapped_column(server_default=text("''"))
    column: Mapped[str] = mapped_column(server_default=text("'backlog'"))
    rank: Mapped[str] = rank()
    blocked_from_column: Mapped[str | None]
    blocked_reason: Mapped[str | None]
    type: Mapped[str | None]
    size: Mapped[str | None]
    risk: Mapped[str | None]
    areas: Mapped[list[str]] = mapped_column(TEXT_ARRAY, server_default=text("'{}'::text[]"))
    labels: Mapped[list[str]] = mapped_column(TEXT_ARRAY, server_default=text("'{}'::text[]"))
    needs_human_design: Mapped[bool] = mapped_column(server_default=text("false"))
    priority: Mapped[str | None]
    severity: Mapped[str | None]
    route: Mapped[str | None]
    out_of_scope_reason: Mapped[str | None]
    pii_redacted: Mapped[bool] = mapped_column(server_default=text("false"))
    pr_url: Mapped[str | None]
    created_by: Mapped[uuid.UUID] = ref("users.id", ondelete="RESTRICT")
    deleted_at: Mapped[datetime | None] = stamp()
    version: Mapped[int] = version()
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (
        one_of("kind", vocab.TASK_EVENT_KIND),
        one_of("actor_kind", vocab.ACTOR_KIND),
        Index("ix_task_events_task_at", "task_id", "at"),
    )

    id: Mapped[uuid.UUID] = pk()
    task_id: Mapped[uuid.UUID] = ref("dev_tasks.id")
    at: Mapped[datetime] = required_stamp()
    kind: Mapped[str]
    actor_kind: Mapped[str]
    actor_user_id: Mapped[uuid.UUID | None] = opt_ref("users.id")
    from_column: Mapped[str | None]
    to_column: Mapped[str | None]
    run_id: Mapped[uuid.UUID | None] = opt_ref("runs.id")
    created_at: Mapped[datetime] = created()


class TaskTriage(Base):
    __tablename__ = "task_triage"
    __table_args__ = (
        _nullable_one_of("type", vocab.TASK_TYPE),
        _nullable_one_of("size", vocab.TASK_SIZE),
        _nullable_one_of("risk", vocab.TASK_RISK),
        _nullable_one_of("priority", vocab.TASK_PRIORITY),
        _nullable_one_of("severity", vocab.TASK_SEVERITY),
        _nullable_one_of("route", vocab.TRIAGE_ROUTE),
        subset_of("areas", vocab.TASK_AREA),
        Index("ix_task_triage_task_created", "task_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = pk()
    task_id: Mapped[uuid.UUID] = ref("dev_tasks.id")
    type: Mapped[str | None]
    size: Mapped[str | None]
    risk: Mapped[str | None]
    areas: Mapped[list[str]] = mapped_column(TEXT_ARRAY, server_default=text("'{}'::text[]"))
    # Triage no longer writes acceptance criteria (the planner does); kept for older rows.
    acceptance_criteria: Mapped[JsonList] = mapped_column(
        JSONB_COL, server_default=text("'[]'::jsonb")
    )
    priority: Mapped[str | None]
    severity: Mapped[str | None]
    impact: Mapped[str | None]
    route: Mapped[str | None]
    route_reason: Mapped[str | None]
    # {"status", "evidence", "duplicate_of", "code_refs"}; null on rows from before 0008.
    verification: Mapped[JsonDict | None] = mapped_column(JSONB_COL)
    likely_paths: Mapped[list[str]] = mapped_column(TEXT_ARRAY, server_default=text("'{}'::text[]"))
    needs_human_design: Mapped[bool] = mapped_column(server_default=text("false"))
    in_scope: Mapped[bool]
    out_of_scope_reason: Mapped[str | None]
    model: Mapped[str | None]
    provider: Mapped[str | None]
    created_at: Mapped[datetime] = created()


class TaskPlan(Base):
    __tablename__ = "task_plans"
    __table_args__ = (
        one_of("mode", vocab.PLAN_MODE),
        one_of("risk", vocab.TASK_RISK),
        Index("ix_task_plans_task_created", "task_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = pk()
    task_id: Mapped[uuid.UUID] = ref("dev_tasks.id")
    mode: Mapped[str]
    subtasks: Mapped[JsonList] = mapped_column(JSONB_COL, server_default=text("'[]'::jsonb"))
    acceptance_criteria: Mapped[JsonList] = mapped_column(
        JSONB_COL, server_default=text("'[]'::jsonb")
    )
    acceptance_tests: Mapped[list[str]] = mapped_column(
        TEXT_ARRAY, server_default=text("'{}'::text[]")
    )
    tests_to_change: Mapped[list[str]] = mapped_column(
        TEXT_ARRAY, server_default=text("'{}'::text[]")
    )
    budget_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    budget_minutes: Mapped[int]
    risk: Mapped[str]
    tests_approved_at: Mapped[datetime | None] = stamp()
    plan_approved_at: Mapped[datetime | None] = stamp()
    approved_by: Mapped[uuid.UUID | None] = opt_ref("users.id")
    version: Mapped[int] = version()
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        one_of("kind", vocab.RUN_KIND),
        one_of("status", vocab.RUN_STATUS),
        CheckConstraint('"round" >= 0 AND "round" <= 2', name="round"),
        Index(
            "uq_runs_one_active_top_level",
            "task_id",
            unique=True,
            postgresql_where=text("parent_run_id IS NULL AND status IN ('queued', 'running')"),
        ),
        Index("ix_runs_task_created", "task_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = pk()
    task_id: Mapped[uuid.UUID] = ref("dev_tasks.id")
    parent_run_id: Mapped[uuid.UUID | None] = opt_ref("runs.id", ondelete="CASCADE")
    kind: Mapped[str]
    agent: Mapped[str | None]
    status: Mapped[str] = mapped_column(server_default=text("'queued'"))
    round: Mapped[int] = mapped_column(server_default=text("0"))
    model: Mapped[str | None]
    provider: Mapped[str | None]
    budget_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), server_default=text("0"))
    outcome: Mapped[str | None]
    bundle_ref: Mapped[str | None]
    started_at: Mapped[datetime | None] = stamp()
    finished_at: Mapped[datetime | None] = stamp()
    created_at: Mapped[datetime] = created()


class RunStep(Base):
    """A step of a dev-board run (shared, `user_id` NULL) or of a research run (owned: RLS
    matches `user_id`). Exactly one of `run_id` and `research_run_id` is set. Insert fires
    `NOTIFY run_steps` with the run's id (migrations 0001, 0002) for the per-process fan-out."""

    __tablename__ = "run_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "seq"),
        UniqueConstraint("research_run_id", "seq"),
        one_of("kind", vocab.STEP_KIND),
        CheckConstraint("num_nonnulls(run_id, research_run_id) = 1", name="one_run"),
        CheckConstraint("(research_run_id IS NULL) = (user_id IS NULL)", name="research_owner"),
    )

    id: Mapped[uuid.UUID] = pk()
    run_id: Mapped[uuid.UUID | None] = opt_ref("runs.id", ondelete="CASCADE")
    research_run_id: Mapped[uuid.UUID | None] = opt_ref("research_runs.id", ondelete="CASCADE")
    user_id: Mapped[uuid.UUID | None] = opt_ref("users.id", ondelete="CASCADE")
    seq: Mapped[int]
    kind: Mapped[str]
    agent: Mapped[str | None]
    tool: Mapped[str | None]
    summary: Mapped[str]
    at: Mapped[datetime] = required_stamp()
    created_at: Mapped[datetime] = created()


class Lease(Base):
    __tablename__ = "leases"
    __table_args__ = (
        one_of("kind", vocab.LEASE_KIND),
        Index(
            "ix_leases_active_path",
            "path",
            postgresql_where=text("released_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = pk()
    run_id: Mapped[uuid.UUID] = ref("runs.id")
    task_id: Mapped[uuid.UUID] = ref("dev_tasks.id")
    path: Mapped[str]
    kind: Mapped[str]
    granted_at: Mapped[datetime] = required_stamp()
    expires_at: Mapped[datetime] = required_stamp()
    heartbeat_at: Mapped[datetime | None] = stamp()
    released_at: Mapped[datetime | None] = stamp()
    created_at: Mapped[datetime] = created()


class LeaseWait(Base):
    __tablename__ = "lease_waits"
    __table_args__ = (Index("ix_lease_waits_run", "run_id"),)

    id: Mapped[uuid.UUID] = pk()
    run_id: Mapped[uuid.UUID] = ref("runs.id")
    path: Mapped[str]
    queued_at: Mapped[datetime] = required_stamp()
    granted_at: Mapped[datetime | None] = stamp()
    created_at: Mapped[datetime] = created()


__all__ = [
    "DevTask",
    "Lease",
    "LeaseWait",
    "Run",
    "RunStep",
    "TaskEvent",
    "TaskPlan",
    "TaskTriage",
]
