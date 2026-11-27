"""The strict `/out/plan.json` parser: the sandboxed dev-lead's output is untrusted, so unknown
keys, wrong types, out-of-range values, oversize files and another task's plan are refused.
The planner writes the acceptance criteria; the acceptance tests are their test ids."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.adapters.agents.plan_file import MAX_PLAN_BYTES, PlanFileError, parse_plan, read_plan
from app.domain.devboard.model import Criterion, Risk, Subtask
from tests.unit.agents_runner.fakes import TASK_ID


def plan_file(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "task_id": str(TASK_ID),
        "mode": "multi",
        "subtasks": [
            {
                "id": "t0",
                "title": "Write the failing tests",
                "agent": "qa-expert",
                "lease_paths": ["backend/tests/unit/"],
                "depends_on": [],
                "test_only": True,
            },
            {
                "id": "s1",
                "title": "Implement",
                "agent": "backend-engineer",
                "lease_paths": ["backend/src/app/domain/"],
                "depends_on": ["t0"],
                "test_only": False,
            },
        ],
        "acceptance_criteria": [
            {"test_id": "backend/tests/unit/test_x.py::passes", "expectation": "It passes."}
        ],
        "tests_to_change": ["backend/tests/unit/test_y.py"],
        "budget_usd": 1.5,
        "budget_minutes": 30,
        "risk": "medium",
    }
    return body | changes


def _raw(body: dict[str, object]) -> bytes:
    return json.dumps(body).encode()


def test_a_valid_plan_becomes_a_draft() -> None:
    draft = parse_plan(_raw(plan_file()), str(TASK_ID))
    assert draft.mode == "multi"
    assert draft.subtasks == (
        Subtask(
            "t0",
            "Write the failing tests",
            "qa-expert",
            ("backend/tests/unit/",),
            is_test_only=True,
        ),
        Subtask("s1", "Implement", "backend-engineer", ("backend/src/app/domain/",), ("t0",)),
    )
    assert draft.acceptance_criteria == (
        Criterion("backend/tests/unit/test_x.py::passes", "It passes."),
    )
    assert draft.acceptance_tests == ("backend/tests/unit/test_x.py::passes",)
    assert draft.tests_to_change == ("backend/tests/unit/test_y.py",)
    assert (draft.budget_usd, draft.budget_minutes, draft.llm_risk) == (1.5, 30, Risk.MEDIUM)


def test_acceptance_tests_are_the_criteria_test_ids_once_each() -> None:
    criteria = [
        _criterion("backend/tests/unit/test_a.py::one", "First."),
        _criterion("web/src/a.test.tsx::shows", "Shows."),
        _criterion("backend/tests/unit/test_a.py::one", "Also this."),
    ]
    draft = parse_plan(_raw(plan_file(acceptance_criteria=criteria)), str(TASK_ID))
    assert len(draft.acceptance_criteria) == 3
    assert draft.acceptance_tests == (
        "backend/tests/unit/test_a.py::one",
        "web/src/a.test.tsx::shows",
    )


def _criterion(test_id: str = "t.py::x", expectation: str = "It works.") -> dict[str, object]:
    return {"test_id": test_id, "expectation": expectation}


def test_an_integer_budget_is_accepted() -> None:
    assert parse_plan(_raw(plan_file(budget_usd=2)), str(TASK_ID)).budget_usd == 2.0


def _subtask(**changes: object) -> list[dict[str, object]]:
    base: dict[str, object] = {
        "id": "s1",
        "title": "Implement",
        "agent": "backend-engineer",
        "lease_paths": ["backend/src/app/domain/"],
        "depends_on": [],
        "test_only": False,
    }
    return [base | changes]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(plan_file(extra="x"), id="unknown-key"),
        pytest.param(plan_file(subtasks=_subtask(owner="me")), id="unknown-subtask-key"),
        pytest.param(plan_file(mode="solo"), id="bad-mode"),
        pytest.param(plan_file(risk="none"), id="bad-risk"),
        pytest.param(plan_file(subtasks=[]), id="no-subtasks"),
        pytest.param(plan_file(subtasks=_subtask() * 21), id="too-many-subtasks"),
        pytest.param(plan_file(subtasks=_subtask(id="x" * 65)), id="long-id"),
        pytest.param(plan_file(subtasks=_subtask(title="x" * 201)), id="long-title"),
        pytest.param(plan_file(subtasks=_subtask(agent="x" * 65)), id="long-agent"),
        pytest.param(plan_file(subtasks=_subtask(lease_paths=["a/"] * 51)), id="many-leases"),
        pytest.param(plan_file(subtasks=_subtask(lease_paths=["a" * 301])), id="long-lease"),
        pytest.param(plan_file(subtasks=_subtask(depends_on=["t0"] * 21)), id="many-deps"),
        pytest.param(plan_file(subtasks=_subtask(depends_on=["nope"])), id="unknown-dep"),
        pytest.param(plan_file(subtasks=_subtask() * 2), id="duplicate-ids"),
        pytest.param(plan_file(subtasks=_subtask(test_only="yes")), id="string-bool"),
        pytest.param(plan_file(acceptance_tests=["t.py::x"]), id="separate-acceptance-tests"),
        pytest.param(plan_file(acceptance_criteria=[]), id="no-criteria"),
        pytest.param(plan_file(acceptance_criteria=[_criterion()] * 31), id="many-criteria"),
        pytest.param(
            plan_file(acceptance_criteria=[_criterion("t.py::" + "x" * 300)]), id="long-test-id"
        ),
        pytest.param(plan_file(acceptance_criteria=[_criterion("t.py")]), id="no-test-name"),
        pytest.param(plan_file(acceptance_criteria=[_criterion(expectation="")]), id="no-expect"),
        pytest.param(
            plan_file(acceptance_criteria=[_criterion(expectation="x" * 1001)]), id="long-expect"
        ),
        pytest.param(
            plan_file(acceptance_criteria=[{**_criterion(), "why": "x"}]), id="criterion-key"
        ),
        pytest.param(plan_file(acceptance_criteria=["t.py::x"]), id="criterion-string"),
        pytest.param(plan_file(tests_to_change=["a.py"] * 51), id="many-changes"),
        pytest.param(plan_file(budget_usd=20.5), id="budget-high"),
        pytest.param(plan_file(budget_usd=-1), id="budget-negative"),
        pytest.param(plan_file(budget_usd="1"), id="budget-string"),
        pytest.param(plan_file(budget_minutes=0), id="minutes-low"),
        pytest.param(plan_file(budget_minutes=121), id="minutes-high"),
        pytest.param(plan_file(budget_minutes=1.5), id="minutes-float"),
        pytest.param(plan_file(budget_minutes=True), id="minutes-bool"),
        pytest.param({k: v for k, v in plan_file().items() if k != "risk"}, id="missing-risk"),
    ],
)
def test_malformed_plans_are_refused(body: dict[str, object]) -> None:
    with pytest.raises(PlanFileError) as caught:
        parse_plan(_raw(body), str(TASK_ID))
    assert str(caught.value) == "The dev-lead's plan is malformed."


@pytest.mark.parametrize("raw", [b"{not json", b"\xff\xfe\x00", b"[]"])
def test_non_json_is_refused(raw: bytes) -> None:
    with pytest.raises(PlanFileError):
        parse_plan(raw, str(TASK_ID))


def test_another_tasks_plan_is_refused() -> None:
    with pytest.raises(PlanFileError, match="another task"):
        parse_plan(_raw(plan_file(task_id="someone-else")), str(TASK_ID))


def test_oversize_plans_are_refused(tmp_path: Path) -> None:
    raw = _raw(plan_file(padding="x" * MAX_PLAN_BYTES))
    with pytest.raises(PlanFileError, match="too large"):
        parse_plan(raw, str(TASK_ID))
    path = tmp_path / "plan.json"
    path.write_bytes(raw)
    with pytest.raises(PlanFileError, match="too large"):
        read_plan(path, str(TASK_ID))


def test_a_missing_plan_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PlanFileError, match="no plan"):
        read_plan(tmp_path / "plan.json", str(TASK_ID))


def _always(_self: Path) -> bool:
    return True


def test_a_symlinked_plan_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The agent controls /out; a link could point anywhere on the host.
    path = tmp_path / "plan.json"
    path.write_bytes(_raw(plan_file()))
    monkeypatch.setattr(Path, "is_symlink", _always)
    with pytest.raises(PlanFileError, match="regular file"):
        read_plan(path, str(TASK_ID))
