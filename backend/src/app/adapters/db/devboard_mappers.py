"""Row <-> record mapping for the dev-board Postgres adapters.

Triage rows from before migration 0008 have no priority, severity, route or verification. They
map to the most cautious reading: P3 / minor, route `next` (no automatic planning), and an
`unclear` verification with no evidence. Their old acceptance criteria are not read: the
planner writes criteria now (`task_plans.acceptance_criteria`).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import cast

from sqlalchemy import Row, Select, select

from app.adapters.db.tables_board import DevTask, Lease, Run, RunStep, TaskPlan, TaskTriage
from app.adapters.db.tables_platform import User
from app.domain.devboard.model import (
    Area,
    Column,
    Criterion,
    LeaseKind,
    LeaseRecord,
    PlanRecord,
    Priority,
    Risk,
    Route,
    RunKind,
    RunRecord,
    RunStatus,
    Severity,
    Size,
    StepKind,
    StepRecord,
    Subtask,
    TaskRecord,
    TaskType,
    TriageRecord,
    Verification,
    VerificationStatus,
)
from app.domain.identity import AppRole

ACTIVE_STATUSES = ("queued", "running")
# Pre-0008 triage rows (see the module docstring).
LEGACY_PRIORITY = Priority.P3
LEGACY_SEVERITY = Severity.MINOR
LEGACY_ROUTE = Route.NEXT


def money(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


def strings(value: object) -> tuple[str, ...]:
    return (
        tuple(str(item) for item in cast("list[object]", value)) if isinstance(value, list) else ()
    )


def fields(item: object) -> dict[str, object] | None:
    return cast("dict[str, object]", item) if isinstance(item, dict) else None


# ------------------------------------------------------------------ row mapping
def task_select() -> Select[DevTask, str, uuid.UUID]:
    active = (
        select(Run.id)
        .where(
            Run.task_id == DevTask.id, Run.parent_run_id.is_(None), Run.status.in_(ACTIVE_STATUSES)
        )
        .limit(1)
        .scalar_subquery()
    )
    return (
        select(DevTask, User.role, active.label("active_run_id"))
        .outerjoin(User, User.id == DevTask.created_by)
        .where(DevTask.deleted_at.is_(None))
    )


def task_record(row: Row[DevTask, str, uuid.UUID]) -> TaskRecord:
    task = row[0]
    return TaskRecord(
        id=task.id,
        title=task.title,
        body=task.body,
        column=Column(task.column),
        rank=task.rank,
        version=task.version,
        created_by=task.created_by,
        created_at=task.created_at,
        updated_at=task.updated_at,
        created_by_role=AppRole(row[1]) if row[1] else None,
        type=TaskType(task.type) if task.type else None,
        size=Size(task.size) if task.size else None,
        risk=Risk(task.risk) if task.risk else None,
        areas=tuple(Area(area) for area in task.areas),
        labels=tuple(task.labels),
        needs_human_design=task.needs_human_design,
        priority=Priority(task.priority) if task.priority else None,
        severity=Severity(task.severity) if task.severity else None,
        route=Route(task.route) if task.route else None,
        out_of_scope_reason=task.out_of_scope_reason,
        blocked_reason=task.blocked_reason,
        blocked_from=Column(task.blocked_from_column) if task.blocked_from_column else None,
        pii_redacted=task.pii_redacted,
        pr_url=task.pr_url,
        branch=task.branch,
        base_sha=task.base_sha,
        pr_branch=task.pr_branch,
        review_round=task.review_round,
        active_run_id=row[2],
    )


def task_values(task: TaskRecord) -> dict[str, object]:
    return {
        "title": task.title,
        "body": task.body,
        "column": task.column.value,
        "rank": task.rank,
        "blocked_from_column": task.blocked_from.value if task.blocked_from else None,
        "blocked_reason": task.blocked_reason,
        "type": task.type.value if task.type else None,
        "size": task.size.value if task.size else None,
        "risk": task.risk.value if task.risk else None,
        "areas": [area.value for area in task.areas],
        "labels": list(task.labels),
        "needs_human_design": task.needs_human_design,
        "priority": task.priority.value if task.priority else None,
        "severity": task.severity.value if task.severity else None,
        "route": task.route.value if task.route else None,
        "out_of_scope_reason": task.out_of_scope_reason,
        "pii_redacted": task.pii_redacted,
        "pr_url": task.pr_url,
        "branch": task.branch,
        "base_sha": task.base_sha,
        "pr_branch": task.pr_branch,
        "review_round": task.review_round,
        "updated_at": task.updated_at,
    }


def verification_record(value: dict[str, object] | None) -> Verification:
    if value is None:
        return Verification(status=VerificationStatus.UNCLEAR, evidence="")
    status = str(value.get("status", ""))
    duplicate_of = value.get("duplicate_of")
    return Verification(
        status=VerificationStatus(status)
        if status in set(VerificationStatus)
        else VerificationStatus.UNCLEAR,
        evidence=str(value.get("evidence") or ""),
        code_refs=strings(value.get("code_refs")),
        duplicate_of=uuid.UUID(duplicate_of) if isinstance(duplicate_of, str) else None,
    )


def verification_values(verification: Verification) -> dict[str, object]:
    return {
        "status": verification.status.value,
        "evidence": verification.evidence,
        "code_refs": list(verification.code_refs),
        "duplicate_of": str(verification.duplicate_of) if verification.duplicate_of else None,
    }


def triage_record(row: TaskTriage) -> TriageRecord:
    return TriageRecord(
        task_id=row.task_id,
        type=TaskType(row.type or "feature"),
        size=Size(row.size or "M"),
        risk=Risk(row.risk or "medium"),
        areas=tuple(Area(area) for area in row.areas),
        priority=Priority(row.priority) if row.priority else LEGACY_PRIORITY,
        severity=Severity(row.severity) if row.severity else LEGACY_SEVERITY,
        impact=row.impact or "",
        route=Route(row.route) if row.route else LEGACY_ROUTE,
        route_reason=row.route_reason or "",
        verification=verification_record(row.verification),
        likely_paths=tuple(row.likely_paths or ()),
        needs_human_design=row.needs_human_design,
        in_scope=row.in_scope,
        out_of_scope_reason=row.out_of_scope_reason,
        created_at=row.created_at,
        model=row.model,
        provider=row.provider,
    )


def triage_values(triage: TriageRecord) -> dict[str, object]:
    return {
        "task_id": triage.task_id,
        "type": triage.type.value,
        "size": triage.size.value,
        "risk": triage.risk.value,
        "areas": [area.value for area in triage.areas],
        "priority": triage.priority.value,
        "severity": triage.severity.value,
        "impact": triage.impact,
        "route": triage.route.value,
        "route_reason": triage.route_reason,
        "verification": verification_values(triage.verification),
        "likely_paths": list(triage.likely_paths),
        "needs_human_design": triage.needs_human_design,
        "in_scope": triage.in_scope,
        "out_of_scope_reason": triage.out_of_scope_reason,
        "model": triage.model,
        "provider": triage.provider,
        "created_at": triage.created_at,
    }


def plan_record(row: TaskPlan) -> PlanRecord:
    subtasks = [fields for fields in map(fields, row.subtasks) if fields is not None]
    criteria = [fields for fields in map(fields, row.acceptance_criteria) if fields is not None]
    return PlanRecord(
        id=row.id,
        task_id=row.task_id,
        version=row.version,
        mode=row.mode,
        subtasks=tuple(
            Subtask(
                id=str(item.get("id", "")),
                title=str(item.get("title", "")),
                agent=str(item.get("agent", "")),
                lease_paths=strings(item.get("lease_paths")),
                depends_on=strings(item.get("depends_on")),
                is_test_only=item.get("test_only") is True,
            )
            for item in subtasks
        ),
        acceptance_tests=tuple(row.acceptance_tests),
        tests_to_change=tuple(row.tests_to_change),
        acceptance_criteria=tuple(
            Criterion(str(item.get("test_id", "")), str(item.get("expectation", "")))
            for item in criteria
        ),
        budget_usd=float(row.budget_usd),
        budget_minutes=row.budget_minutes,
        risk=Risk(row.risk),
        created_at=row.created_at,
        tests_approved_at=row.tests_approved_at,
        plan_approved_at=row.plan_approved_at,
        approved_by=row.approved_by,
    )


def plan_values(plan: PlanRecord) -> dict[str, object]:
    return {
        "mode": plan.mode,
        "subtasks": [
            {
                "id": s.id,
                "title": s.title,
                "agent": s.agent,
                "lease_paths": list(s.lease_paths),
                "depends_on": list(s.depends_on),
                "test_only": s.is_test_only,
            }
            for s in plan.subtasks
        ],
        "acceptance_tests": list(plan.acceptance_tests),
        "tests_to_change": list(plan.tests_to_change),
        "acceptance_criteria": [
            {"test_id": c.test_id, "expectation": c.expectation} for c in plan.acceptance_criteria
        ],
        "budget_usd": money(plan.budget_usd),
        "budget_minutes": plan.budget_minutes,
        "risk": plan.risk.value,
        "tests_approved_at": plan.tests_approved_at,
        "plan_approved_at": plan.plan_approved_at,
        "approved_by": plan.approved_by,
    }


def run_record(row: Run) -> RunRecord:
    return RunRecord(
        id=row.id,
        task_id=row.task_id,
        kind=RunKind(row.kind),
        status=RunStatus(row.status),
        created_at=row.created_at,
        parent_run_id=row.parent_run_id,
        agent=row.agent,
        round=row.round,
        model=row.model,
        provider=row.provider,
        outcome=row.outcome,
        budget_usd=float(row.budget_usd) if row.budget_usd is not None else None,
        cost_usd=float(row.cost_usd),
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def step_record(row: RunStep) -> StepRecord:
    """Board steps only: the store's queries filter on `run_id`, so it is always set."""
    assert row.run_id is not None
    return StepRecord(
        id=row.id,
        run_id=row.run_id,
        seq=row.seq,
        kind=StepKind(row.kind),
        summary=row.summary,
        at=row.at,
        agent=row.agent,
        tool=row.tool,
    )


def lease_record(row: Lease) -> LeaseRecord:
    return LeaseRecord(
        id=row.id,
        run_id=row.run_id,
        task_id=row.task_id,
        path=row.path,
        kind=LeaseKind(row.kind),
        granted_at=row.granted_at,
        expires_at=row.expires_at,
        heartbeat_at=row.heartbeat_at,
        released_at=row.released_at,
    )
