"""Domain records -> generated response models. Absent values are omitted ("no nulls")."""

from __future__ import annotations

from app.domain.devboard.model import (
    COLUMN_ORDER,
    Actor,
    PlanRecord,
    RunRecord,
    StepRecord,
    TaskEventRecord,
    TaskRecord,
    TriageRecord,
    Verification,
)
from app.domain.devboard.state_machine import user_targets
from app.generated.models import (
    AcceptanceCriterion,
    Plan,
    Run,
    Step,
    Task,
    TaskEvent,
    TriageResult,
)

# The spec's `LlmProvider` has no value for the offline simulation.
_PROVIDERS = frozenset({"vertex-claude", "vertex-gemini", "vertex-maas", "replay"})


def _provider(value: str | None) -> str | None:
    return value if value in _PROVIDERS else None


def task(record: TaskRecord, viewer: Actor) -> Task:
    """`allowed_columns`: where `viewer` may move the card now (the state machine's user
    targets for their role), so the board can mark the other columns as not allowed."""
    allowed = user_targets(record, is_maintainer=viewer.is_maintainer) - {record.column}
    return Task.model_validate(
        {
            "id": record.id,
            "title": record.title,
            "body": record.body or None,
            "column": record.column.value,
            "rank": record.rank,
            "version": record.version,
            "type": record.type.value if record.type else None,
            "size": record.size.value if record.size else None,
            "risk": record.risk.value if record.risk else None,
            "areas": [area.value for area in record.areas] or None,
            "labels": list(record.labels) or None,
            "needs_human_design": record.needs_human_design,
            "priority": record.priority.value if record.priority else None,
            "severity": record.severity.value if record.severity else None,
            "route": record.route.value if record.route else None,
            "allowed_columns": [c.value for c in COLUMN_ORDER if c in allowed],
            "out_of_scope_reason": record.out_of_scope_reason,
            "blocked_reason": record.blocked_reason,
            "pii_redacted": record.pii_redacted,
            "active_run_id": record.active_run_id,
            "pr_url": record.pr_url,
            "created_by_role": record.created_by_role.value if record.created_by_role else None,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
    )


def event(record: TaskEventRecord) -> TaskEvent:
    return TaskEvent.model_validate(
        {
            "id": record.id,
            "task_id": record.task_id,
            "at": record.at,
            "kind": record.kind.value,
            "actor": record.actor_kind.value,
            "from_column": record.from_column.value if record.from_column else None,
            "to_column": record.to_column.value if record.to_column else None,
            "run_id": record.run_id,
        }
    )


def triage(record: TriageRecord) -> TriageResult:
    return TriageResult.model_validate(
        {
            "task_id": record.task_id,
            "type": record.type.value,
            "size": record.size.value,
            "risk": record.risk.value,
            "areas": [area.value for area in record.areas],
            "priority": record.priority.value,
            "severity": record.severity.value,
            "impact": record.impact,
            "route": record.route.value,
            "route_reason": record.route_reason,
            "verification": _verification(record.verification),
            "likely_paths": list(record.likely_paths),
            "needs_human_design": record.needs_human_design,
            "in_scope": record.in_scope,
            "out_of_scope_reason": record.out_of_scope_reason,
            "model": record.model,
            "provider": _provider(record.provider),
            "created_at": record.created_at,
        }
    )


def _verification(record: Verification) -> dict[str, object]:
    return {
        "status": record.status.value,
        "evidence": record.evidence,
        "code_refs": list(record.code_refs),
        "duplicate_of": record.duplicate_of,
    }


def plan(record: PlanRecord) -> Plan:
    return Plan.model_validate(
        {
            "task_id": record.task_id,
            "version": record.version,
            "mode": record.mode,
            "subtasks": [
                {
                    "id": s.id,
                    "title": s.title,
                    "agent": s.agent,
                    "lease_paths": list(s.lease_paths),
                    "depends_on": list(s.depends_on) or None,
                    "test_only": s.is_test_only or None,
                }
                for s in record.subtasks
            ],
            "acceptance_criteria": [
                AcceptanceCriterion(test_id=c.test_id, expectation=c.expectation)
                for c in record.acceptance_criteria
            ],
            "acceptance_tests": list(record.acceptance_tests),
            "tests_to_change": list(record.tests_to_change),
            "budget": {"max_usd": record.budget_usd, "max_minutes": record.budget_minutes},
            "risk": record.risk.value,
            "tests_approved": record.tests_approved_at is not None,
            "plan_approved": record.plan_approved_at is not None,
            "approved_at": record.plan_approved_at,
            "created_at": record.created_at,
        }
    )


def run(record: RunRecord) -> Run:
    return Run.model_validate(
        {
            "id": record.id,
            "task_id": record.task_id,
            "parent_run_id": record.parent_run_id,
            "kind": record.kind.value,
            "agent": record.agent,
            "status": record.status.value,
            "round": record.round,
            "model": record.model,
            "provider": _provider(record.provider),
            "outcome": record.outcome,
            "budget_usd": record.budget_usd,
            "cost_usd": record.cost_usd,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "created_at": record.created_at,
        }
    )


def step(record: StepRecord, summary: str) -> Step:
    return Step.model_validate(
        {
            "id": record.id,
            "run_id": record.run_id,
            "seq": record.seq,
            "kind": record.kind.value,
            "agent": record.agent,
            "tool": record.tool,
            "summary": summary,
            "at": record.at,
        }
    )
