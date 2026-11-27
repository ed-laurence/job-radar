"""The /out/plan.json contract the backend parses, checked before the file is written."""

from __future__ import annotations

import json
from pathlib import Path

import plan_contract as pc
import pytest

TASK = "T-1"


def _repo(tmp_path: Path) -> Path:
    files = [
        "api/openapi.yaml",
        "backend/src/app/api/board.py",
        "backend/src/app/generated/models.py",
        "backend/tests/unit/devboard/test_board.py",
        "backend/tests/contract/test_api.py",
        "backend/tests/integration/test_db.py",
        "web/src/features/board/Card.tsx",
        "web/src/features/board/Card.test.tsx",
        "web/src/lib/api/generated/schema.d.ts",
    ]
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n")
    return tmp_path


def _plan(**overrides: object) -> dict[str, object]:
    plan: dict[str, object] = {
        "task_id": TASK,
        "mode": "single",
        "subtasks": [
            {
                "id": "s1",
                "title": "Card badge",
                "agent": "frontend-engineer",
                "lease_paths": ["web/src/features/board/"],
                "depends_on": [],
                "test_only": False,
            }
        ],
        "acceptance_criteria": [
            {
                "test_id": "web/src/features/board/Card.test.tsx::shows the badge",
                "expectation": "The card shows a priority badge.",
            }
        ],
        "tests_to_change": ["web/src/features/board/Card.test.tsx"],
        "budget_usd": 2.5,
        "budget_minutes": 30,
        "risk": "low",
    }
    plan.update(overrides)
    return plan


def _sub(**overrides: object) -> dict[str, object]:
    sub: dict[str, object] = {
        "id": "s1",
        "title": "t",
        "agent": "backend-engineer",
        "lease_paths": ["backend/src/app/api/"],
    }
    sub.update(overrides)
    return sub


def _crit(test_id: str, expectation: str = "It works.") -> dict[str, object]:
    return {"test_id": test_id, "expectation": expectation}


def _crits(test_id: str, expectation: str = "It works.") -> list[dict[str, object]]:
    return [_crit(test_id, expectation)]


def _errors(raw: object, repo: Path | None = None) -> str:
    with pytest.raises(pc.PlanError) as info:
        pc.validate(raw, TASK, repo)
    assert info.value.errors
    return " | ".join(info.value.errors)


def test_a_valid_plan_is_returned_in_the_contract_shape(tmp_path: Path) -> None:
    plan = pc.validate(_plan(), TASK, _repo(tmp_path))
    assert plan == _plan()
    assert list(plan) == list(pc.TOP_KEYS)


def test_defaults_are_filled_so_the_file_always_has_every_key() -> None:
    raw = _plan(subtasks=[_sub()])
    del raw["task_id"], raw["tests_to_change"]
    plan = pc.validate(raw, TASK)
    assert plan["task_id"] == TASK and plan["tests_to_change"] == []
    (sub,) = pc.as_dicts(plan["subtasks"])
    assert sub["depends_on"] == [] and sub["test_only"] is False
    assert list(sub) == list(pc.SUBTASK_KEYS)


@pytest.mark.parametrize(
    ("raw", "needle"),
    [
        ("not an object", "object"),
        (_plan(extra=1), "unknown key"),
        (_plan(task_id="T-2"), "task_id"),
        (_plan(mode="parallel"), "mode"),
        (_plan(subtasks=[]), "subtasks"),
        (_plan(subtasks=[_sub(id=f"s{i}") for i in range(21)]), "subtasks"),
        (_plan(subtasks=[_sub(colour="red")]), "unknown key"),
        (_plan(subtasks=[_sub(id="x" * 65)]), "id"),
        (_plan(subtasks=[_sub(title="x" * 201)]), "title"),
        (_plan(subtasks=[_sub(agent="")]), "agent"),
        (_plan(subtasks=[_sub(lease_paths=[])]), "lease_paths"),
        (_plan(subtasks=[_sub(lease_paths=[f"web/f{i}.ts" for i in range(51)])]), "lease_paths"),
        (_plan(subtasks=[_sub(lease_paths=["x" * 301])]), "lease_paths"),
        (_plan(subtasks=[_sub(lease_paths=["/etc/passwd"])]), "repo-relative"),
        (_plan(subtasks=[_sub(lease_paths=["web/**/*.ts"])]), "glob"),
        (_plan(subtasks=[_sub(lease_paths=["../x"])]), "repo-relative"),
        (_plan(subtasks=[_sub(depends_on=["s9"])]), "depends_on"),
        (_plan(subtasks=[_sub(depends_on=["s1"])]), "depends_on"),
        (_plan(subtasks=[_sub(test_only="yes")]), "test_only"),
        (_plan(subtasks=[_sub(), _sub()]), "duplicate"),
        (_plan(acceptance_tests=["web/src/a.test.tsx::x"]), "unknown key"),
        (_plan(acceptance_criteria=[]), "acceptance_criteria"),
        (_plan(acceptance_criteria=["web/src/a.test.tsx::x"]), "object"),
        (_plan(acceptance_criteria=_crits("web/src/a.test.tsx")), "::"),
        (_plan(acceptance_criteria=_crits("web/src/a.test.tsx::" + "x" * 300)), "test_id"),
        (_plan(acceptance_criteria=_crits("web/src/a.test.tsx::x", "")), "expectation"),
        (_plan(acceptance_criteria=_crits("web/src/a.test.tsx::x", "x" * 1001)), "expectation"),
        (_plan(acceptance_criteria=_crits("backend/src/app/x.py::x")), "not a test file"),
        (
            _plan(acceptance_criteria=[_crit(f"backend/tests/t{i}.py::x") for i in range(31)]),
            "1-30",
        ),
        (_plan(acceptance_criteria=[{**_crit("web/src/a.test.tsx::x"), "why": 1}]), "unknown key"),
        (_plan(tests_to_change=[f"backend/tests/t{i}.py" for i in range(51)]), "tests_to_change"),
        (_plan(tests_to_change=["/abs/test_x.py"]), "repo-relative"),
        (_plan(budget_usd=20.5), "budget_usd"),
        (_plan(budget_usd=True), "budget_usd"),
        (_plan(budget_usd="2"), "budget_usd"),
        (_plan(budget_minutes=0), "budget_minutes"),
        (_plan(budget_minutes=121), "budget_minutes"),
        (_plan(budget_minutes=30.0), "budget_minutes"),
        (_plan(risk="extreme"), "risk"),
    ],
)
def test_contract_violations_are_refused(raw: object, needle: str) -> None:
    assert needle in _errors(raw)


def test_paths_are_checked_against_the_real_tree(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    text = _errors(
        _plan(
            subtasks=[_sub(lease_paths=["backend/src/nowhere/deep/x.py"])],
            acceptance_criteria=_crits("backend/tests/nowhere/test_x.py::test_a"),
            tests_to_change=["backend/tests/unit/devboard/test_missing.py"],
        ),
        repo,
    )
    assert "backend/src/nowhere/deep/x.py" in text
    assert "backend/tests/nowhere/test_x.py" in text
    assert "backend/tests/unit/devboard/test_missing.py" in text


def test_new_files_in_existing_dirs_are_fine(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    plan = _plan(
        subtasks=[_sub(lease_paths=["backend/src/app/api/new_route.py", "web/src/features/x/"])],
        acceptance_criteria=_crits("backend/tests/unit/devboard/test_new.py::test_a"),
        tests_to_change=[],
    )
    checked = pc.validate(plan, TASK, repo)
    assert checked["acceptance_criteria"] == plan["acceptance_criteria"]


def test_web_unit_tests_sit_beside_the_component_as_test_tsx(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    text = _errors(
        _plan(acceptance_criteria=_crits("web/src/features/board/CardSpec.tsx::x")), repo
    )
    assert ".test.ts" in text


def test_tests_to_change_must_be_existing_test_files(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert "test file" in _errors(_plan(tests_to_change=["web/src/features/board/Card.tsx"]), repo)


def test_an_api_change_needs_generated_dirs_and_contract_and_integration_tests(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    api_only = _plan(
        mode="multi",
        subtasks=[
            _sub(id="tests", lease_paths=["backend/tests/unit/"], test_only=True),
            _sub(id="api", lease_paths=["api/openapi.yaml", "backend/src/app/api/"]),
        ],
        acceptance_criteria=_crits("backend/tests/unit/devboard/test_board.py::test_a"),
        tests_to_change=[],
    )
    text = _errors(api_only, repo)
    for needed in (
        "backend/src/app/generated",
        "web/src/lib/api/generated",
        "backend/tests/contract",
        "backend/tests/integration",
    ):
        assert needed in text
    fixed = _plan(
        mode="multi",
        subtasks=[
            _sub(
                id="tests",
                lease_paths=[
                    "backend/tests/unit/",
                    "backend/tests/contract/",
                    "backend/tests/integration/",
                ],
                test_only=True,
            ),
            _sub(
                id="api",
                lease_paths=[
                    "api/openapi.yaml",
                    "backend/src/app/api/",
                    "backend/src/app/generated/",
                    "web/src/lib/api/generated/",
                ],
                depends_on=["tests"],
            ),
        ],
        acceptance_criteria=_crits("backend/tests/contract/test_api.py::test_new_route"),
        tests_to_change=[],
    )
    assert pc.validate(fixed, TASK, repo)["mode"] == "multi"


def test_errors_are_capped_and_never_echo_huge_input() -> None:
    raw = _plan(subtasks=[_sub(id=f"s{i}", agent="", title="x" * 5000) for i in range(20)])
    with pytest.raises(pc.PlanError) as info:
        pc.validate(raw, TASK)
    assert len(info.value.errors) <= pc.MAX_ERRORS
    assert "x" * 400 not in str(info.value)


def test_acceptance_tests_are_the_criteria_test_ids_once_each() -> None:
    criteria = [
        _crit("backend/tests/unit/test_a.py::test_x", "First."),
        _crit("backend/tests/unit/test_a.py::test_x", "Same test, second expectation."),
        _crit("web/src/a.test.tsx::shows", "Shows."),
    ]
    plan = pc.validate(_plan(acceptance_criteria=criteria), TASK)
    assert pc.acceptance_tests(plan) == [
        "backend/tests/unit/test_a.py::test_x",
        "web/src/a.test.tsx::shows",
    ]


def test_write_is_atomic_json(tmp_path: Path) -> None:
    out = tmp_path / "plan.json"
    pc.write(pc.validate(_plan(), TASK), out)
    assert json.loads(out.read_text(encoding="utf-8")) == _plan()
    assert not (tmp_path / "plan.json.tmp").exists()
