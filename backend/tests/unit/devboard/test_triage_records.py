"""Storing and showing the new triage and plan fields: row mappers (old rows without the 0008
columns read cautiously), the API mapping, the planner's acceptance criteria, and the offline
synthetic triage answer."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.adapters.db.devboard_mappers import (
    LEGACY_PRIORITY,
    LEGACY_ROUTE,
    LEGACY_SEVERITY,
    plan_record,
    plan_values,
    triage_record,
    triage_values,
    verification_record,
    verification_values,
)
from app.adapters.db.tables_board import TaskPlan, TaskTriage
from app.adapters.llm.providers.base import ProviderRequest
from app.adapters.llm.providers.synthetic import SyntheticProvider
from app.agents.triage.agent import path_problems
from app.agents.triage.schema import TriageOutput
from app.api.devboard import mapping
from app.domain.devboard.model import (
    Area,
    Column,
    Criterion,
    PlanRecord,
    Priority,
    Risk,
    Route,
    Severity,
    Size,
    Subtask,
    TaskRecord,
    TaskType,
    TriageRecord,
    Verification,
    VerificationStatus,
)
from app.domain.devboard.plans import finalise_draft, plan_paths
from app.domain.devboard.repo_paths import unknown_paths
from app.domain.devboard.simulation import draft_criteria, draft_plan
from app.domain.errors import ValidationFailed
from tests.unit.devboard.workflow_harness import FILES, MAINTAINER, draft

NOW = datetime(2026, 11, 25, tzinfo=UTC)
TASK_ID = uuid.UUID(int=5)
TRIAGE = TriageRecord(
    task_id=TASK_ID,
    type=TaskType.SECURITY,
    size=Size.S,
    risk=Risk.MEDIUM,
    areas=(Area.API,),
    priority=Priority.P0,
    severity=Severity.CRITICAL,
    impact="Every account can read other cards.",
    route=Route.NOW,
    route_reason="A confirmed security hole.",
    verification=Verification(
        VerificationStatus.DUPLICATE,
        "Same as the open card.",
        ("backend/src/app/api/devboard/tasks.py",),
        uuid.UUID(int=9),
    ),
    needs_human_design=False,
    in_scope=True,
    created_at=NOW,
    model="claude-sonnet-5-5",
    provider="replay",
)


def _row(values: dict[str, object]) -> TaskTriage:
    return TaskTriage(id=uuid.UUID(int=6), **values)


def test_triage_round_trips_through_the_row() -> None:
    assert triage_record(_row(triage_values(TRIAGE))) == TRIAGE


def test_verification_json_round_trips() -> None:
    values = verification_values(TRIAGE.verification)
    assert values["duplicate_of"] == str(uuid.UUID(int=9))
    assert verification_record(values) == TRIAGE.verification


def test_rows_from_before_0008_read_cautiously() -> None:
    old = _row(
        {
            "task_id": TASK_ID,
            "type": "feature",
            "size": "M",
            "risk": "low",
            "areas": ["web"],
            "acceptance_criteria": [{"test_id": "web/tests/e2e/a.spec.ts::b", "expectation": "c"}],
            "needs_human_design": False,
            "in_scope": True,
            "created_at": NOW,
        }
    )
    record = triage_record(old)
    assert (record.priority, record.severity, record.route) == (
        LEGACY_PRIORITY,
        LEGACY_SEVERITY,
        LEGACY_ROUTE,
    )
    assert (LEGACY_PRIORITY, LEGACY_SEVERITY, LEGACY_ROUTE) == (
        Priority.P3,
        Severity.MINOR,
        Route.NEXT,
    )
    assert record.verification == Verification(VerificationStatus.UNCLEAR, "")
    assert (record.impact, record.route_reason) == ("", "")


def test_an_unknown_verification_status_reads_as_unclear() -> None:
    assert verification_record({"status": "maybe"}).status is VerificationStatus.UNCLEAR


def test_the_api_shows_the_triage_without_criteria() -> None:
    body = mapping.triage(TRIAGE).model_dump(mode="json", exclude_none=True)
    assert "acceptance_criteria" not in body
    assert (body["type"], body["priority"], body["severity"], body["route"]) == (
        "security",
        "P0",
        "critical",
        "now",
    )
    assert body["verification"] == {
        "status": "duplicate",
        "evidence": "Same as the open card.",
        "code_refs": ["backend/src/app/api/devboard/tasks.py"],
        "duplicate_of": str(uuid.UUID(int=9)),
    }


def test_the_api_task_carries_priority_severity_and_route() -> None:
    task = TaskRecord(
        id=TASK_ID,
        title="t",
        body="",
        column=Column.TRIAGED,
        rank="U",
        version=1,
        created_by=uuid.UUID(int=1),
        created_at=NOW,
        updated_at=NOW,
        priority=Priority.P1,
        severity=Severity.MAJOR,
        route=Route.LATER,
    )
    body = mapping.task(task, MAINTAINER).model_dump(mode="json", exclude_none=True)
    assert (body["priority"], body["severity"], body["route"]) == ("P1", "major", "later")


PLAN = PlanRecord(
    id=uuid.UUID(int=7),
    task_id=TASK_ID,
    version=1,
    mode="single",
    subtasks=(Subtask("t0", "Tests", "qa-expert", ("web/tests/e2e/",), is_test_only=True),),
    acceptance_tests=("web/tests/e2e/a.spec.ts::b",),
    budget_usd=1.0,
    budget_minutes=30,
    risk=Risk.LOW,
    created_at=NOW,
    acceptance_criteria=(Criterion("web/tests/e2e/a.spec.ts::b", "the card stays"),),
)


def test_plan_criteria_round_trip_and_reach_the_api() -> None:
    row = TaskPlan(
        id=PLAN.id, task_id=TASK_ID, version=1, created_at=NOW, updated_at=NOW, **plan_values(PLAN)
    )
    assert plan_record(row) == PLAN
    body = mapping.plan(PLAN).model_dump(mode="json")
    assert body["acceptance_criteria"] == [
        {"test_id": "web/tests/e2e/a.spec.ts::b", "expectation": "the card stays"}
    ]


def test_a_criterion_outside_the_test_dirs_refuses_the_draft() -> None:
    bad = draft(acceptance_criteria=(Criterion("web/src/features/A.tsx::renders", "x"),))
    with pytest.raises(ValidationFailed) as caught:
        finalise_draft(bad)
    assert "acceptance criterion" in str(caught.value.detail)


def test_plan_paths_are_criteria_files_then_leases() -> None:
    assert plan_paths(draft()) == (
        "web/tests/e2e/devboard-board.spec.ts",
        "web/tests/e2e/",
        "web/src/features/devboard/",
    )


def test_the_simulated_planner_writes_criteria_from_the_cards_bullets() -> None:
    task = TaskRecord(
        id=TASK_ID,
        title="Search",
        body="Find contacts.\n\n## Acceptance criteria\n- Matches part of a name\n- Newest first",
        column=Column.PLAN_REVIEW,
        rank="U",
        version=1,
        created_by=uuid.UUID(int=1),
        created_at=NOW,
        updated_at=NOW,
    )
    criteria = draft_criteria(task, Area.API)
    assert [c.expectation for c in criteria] == ["Matches part of a name", "Newest first"]
    assert criteria[0].test_id == (
        "backend/tests/unit/test_acceptance.py::test_1_matches_part_of_a_name"
    )
    plan = draft_plan(task, TRIAGE)
    assert plan.acceptance_tests == tuple(c.test_id for c in criteria)
    finalise_draft(plan)  # the planner's own draft is never refused
    assert unknown_paths(plan_paths(plan), FILES | {"backend/tests/unit/test_x.py"}) == ()


async def _synthetic_triage() -> TriageOutput:
    request = ProviderRequest(
        feature="triage",
        prompt_hash="0" * 32,
        model="m",
        effort="low",
        system="s",
        user="u",
        max_tokens=256,
        json_schema=TriageOutput.model_json_schema(),
    )
    text = (await SyntheticProvider().complete(request)).text
    return TriageOutput.model_validate_json(text)


async def test_offline_synthetic_triage_is_valid_calm_and_names_real_paths() -> None:
    output = await _synthetic_triage()
    assert (output.route, output.priority, output.severity) == ("next", "P3", "minor")
    assert output.verification.duplicate_of is None
    assert not output.out_of_scope
    assert path_problems(output, FILES) == ()
