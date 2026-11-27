"""Domain records -> generated response models. Absent values are omitted ("no nulls")."""

from __future__ import annotations

from app.domain.devboard.model import (
    PlanRecord,
    RunRecord,
    StepRecord,
    TaskEventRecord,
    TaskRecord,
    TriageRecord,
)
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


def task(record: TaskRecord) -> Task:
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
            "acceptance_criteria": [
                AcceptanceCriterion(test_id=c.test_id, expectation=c.expectation)
                for c in record.criteria
            ],
            "needs_human_design": record.needs_human_design,
            "in_scope": record.in_scope,
            "out_of_scope_reason": record.out_of_scope_reason,
            "model": record.model,
            "provider": _provider(record.provider),
            "created_at": record.created_at,
        }
    )


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
            "acceptance_tests": list(record.acceptance_tests),
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
