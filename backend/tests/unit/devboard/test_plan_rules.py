"""Code decides the plan, not the model: contract leases bring the regenerated code and the
contract tests, unusable acceptance tests or test changes refuse the draft, and approving a
plan that edits existing tests needs `allow_test_changes` (which adds `modify-tests:` labels)."""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from app.adapters.fakes.devboard import InMemoryTaskStore
from app.adapters.fakes.security import FakePII
from app.adapters.fakes.system import FakeClock, SequentialIds
from app.domain.devboard.board import MAX_LABEL_CHARS, MAX_LABELS, BoardService
from app.domain.devboard.model import (
    Actor,
    ActorKind,
    Column,
    PlanRecord,
    Risk,
    Subtask,
    TaskRecord,
)
from app.domain.devboard.paths import (
    REGENERATED,
    is_leasable,
    is_regenerated,
    is_test_file,
    is_writable,
)
from app.domain.devboard.plans import PlanService, finalise_draft, plan_risk, uncovered_tests
from app.domain.errors import ValidationFailed
from app.domain.identity import AppRole
from app.domain.ports.devboard import PlanDraft

MAINTAINER = Actor(uuid.UUID(int=1), ActorKind.MAINTAINER, AppRole.MAINTAINER)
TEST = "backend/tests/unit/test_x.py::passes"


def _draft(
    *subtasks: Subtask,
    tests: tuple[str, ...] = (TEST,),
    to_change: tuple[str, ...] = (),
) -> PlanDraft:
    chosen = subtasks or (
        Subtask("t0", "Tests", "qa-expert", ("backend/tests/unit/",), is_test_only=True),
        Subtask("s1", "Build", "backend-engineer", ("backend/src/app/domain/",), ("t0",)),
    )
    return PlanDraft(
        mode="multi",
        subtasks=chosen,
        acceptance_tests=tests,
        budget_usd=1.0,
        budget_minutes=30,
        llm_risk=Risk.LOW,
        tests_to_change=to_change,
    )


# ------------------------------------------------------------------ paths
@pytest.mark.parametrize("path", [*REGENERATED, "backend/src/app/generated/models.py"])
def test_regenerated_code_is_leasable_but_never_hand_writable(path: str) -> None:
    assert is_regenerated(path)
    assert is_leasable(path)
    assert not is_writable(path)


def test_regenerated_prefixes_cover_both_generated_trees() -> None:
    assert REGENERATED == ("backend/src/app/generated/", "web/src/lib/api/generated/")
    assert is_writable("web/src/lib/api/client.ts")
    assert not is_writable("web/src/lib/api/generated/schema.d.ts")


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("backend/tests/unit/devboard/test_board_api.py", True),
        ("backend/tests/integration/test_database.py", True),
        ("backend/tests/contract/devboard/test_tasks.py", True),
        ("web/tests/board.spec.ts", True),
        ("web/src/features/contacts/ContactsPage.test.tsx", True),
        ("backend/tests/unit/conftest.py", False),
        ("backend/tests/unit/devboard/support.py", False),
        ("backend/tests/unit/", False),
        ("backend/tests/evals/test_triage.py", False),
        ("backend/tests/fixtures/llm/x.json", False),
        ("web/src/features/contacts/ContactsPage.tsx", False),
        ("web/playwright.config.ts", False),
        ("backend/src/app/domain/test_x.py", False),
    ],
)
def test_test_files_are_test_style_names_in_test_dirs(path: str, expected: bool) -> None:
    assert is_test_file(path) is expected


# ------------------------------------------------------------------ drafts
def test_contract_lease_brings_regenerated_code_and_contract_tests() -> None:
    draft = _draft(
        Subtask("t0", "Tests", "qa-expert", ("backend/tests/unit/",), is_test_only=True),
        Subtask("c1", "Contract", "backend-engineer", ("api/openapi.yaml",), ("t0",)),
        Subtask("s1", "Build", "backend-engineer", ("backend/src/app/api/",), ("c1",)),
    )
    final = finalise_draft(draft)
    by_id = {s.id: s for s in final.subtasks}
    assert by_id["c1"].lease_paths == ("api/openapi.yaml", *REGENERATED)
    assert by_id["t0"].lease_paths == ("backend/tests/unit/", "backend/tests/contract/")
    assert by_id["s1"].lease_paths == ("backend/src/app/api/",)
    assert plan_risk(final) is Risk.MEDIUM  # contract + generated code are medium


def test_a_directory_lease_over_the_contract_counts_and_nothing_is_doubled() -> None:
    draft = _draft(
        Subtask("t0", "Tests", "qa-expert", ("backend/tests/",), is_test_only=True),
        Subtask("c1", "Contract", "backend-engineer", ("api/", "backend/src/app/generated/")),
    )
    final = finalise_draft(draft)
    assert final.subtasks[1].lease_paths == ("api/", *REGENERATED)
    assert final.subtasks[0].lease_paths == ("backend/tests/",)


def test_without_a_contract_lease_the_draft_is_unchanged() -> None:
    draft = _draft()
    assert finalise_draft(draft) == draft


@pytest.mark.parametrize(
    "test_id",
    [
        "backend/tests/unit/conftest.py::fixture",
        "infra/tests/test_x.py::y",
        "backend/tests/unit/test_x.py",
        "backend/../infra/test_x.py::y",
        "backend/tests/evals/test_x.py::y",
    ],
)
def test_acceptance_tests_must_live_in_writable_test_files(test_id: str) -> None:
    with pytest.raises(ValidationFailed) as caught:
        finalise_draft(_draft(tests=(test_id,)))
    detail = caught.value.detail or ""
    assert "acceptance test" in detail
    assert "\n" not in detail


@pytest.mark.parametrize(
    "path",
    ["backend/tests/unit/conftest.py", "backend/tests/unit/", "backend/src/app/domain/x.py"],
)
def test_tests_to_change_must_be_existing_test_files(path: str) -> None:
    with pytest.raises(ValidationFailed) as caught:
        finalise_draft(_draft(to_change=(path,)))
    assert "existing test" in (caught.value.detail or "")


def test_tests_to_change_are_normalised_and_deduplicated() -> None:
    draft = _draft(
        to_change=("backend\\tests\\unit\\test_x.py", "backend/tests/unit/test_x.py"),
    )
    assert finalise_draft(draft).tests_to_change == ("backend/tests/unit/test_x.py",)


def test_too_many_lease_paths_after_the_contract_additions_refuse_the_draft() -> None:
    many = tuple(f"backend/src/app/api/m{i}.py" for i in range(49))
    draft = _draft(Subtask("c1", "Contract", "backend-engineer", ("api/openapi.yaml", *many)))
    with pytest.raises(ValidationFailed):
        finalise_draft(draft)


# ------------------------------------------------------------------ approval
def test_uncovered_tests_respect_modify_tests_labels() -> None:
    combined = "modify-tests:backend/tests/unit/devboard/,web/tests/a.spec.ts"
    labels = ("ux", combined, "modify-tests:")
    paths = (
        "backend/tests/unit/devboard/test_x.py",
        "web/tests/a.spec.ts",
        "web/tests/b.spec.ts",
    )
    assert uncovered_tests(paths, labels) == ("web/tests/b.spec.ts",)


@pytest.mark.parametrize(
    ("label", "covered"),
    [
        ("modify-tests: ./backend/tests/unit// ", True),
        ("modify-tests:backend/tests/unit/test_x.py", True),
        ("modify-tests:backend/tests/un", False),
        ("modify-tests:backend/tests/unit/test_x", False),
        ("modify-test:backend/tests/unit/", False),
        ("modify-tests:web/tests/, backend/tests/", True),
    ],
)
def test_coverage_follows_the_web_rule(label: str, covered: bool) -> None:
    # web/src/features/devboard/testChanges.ts: exact match, or an entry then "/".
    path = "backend/tests/unit/test_x.py"
    assert (uncovered_tests((path,), (label,)) == ()) is covered


class Gate:
    def __init__(self) -> None:
        self.store = InMemoryTaskStore()
        clock, ids = FakeClock(), SequentialIds()
        self.board = BoardService(self.store, FakePII(), clock, ids)
        self.plans = PlanService(self.store, self.board, clock, ids)

    async def card(self, labels: tuple[str, ...], to_change: tuple[str, ...]) -> TaskRecord:
        task = await self.board.create(MAINTAINER, "Change a field", "", labels)
        draft = _draft(to_change=to_change)
        await self.plans.save_draft(MAINTAINER, task, draft)
        moved = replace(task, column=Column.PLAN_REVIEW)
        return await self.store.save(MAINTAINER.user_id, moved, task.version)

    async def approve(self, task: TaskRecord, *, allow: bool) -> TaskRecord:
        return await self.plans.approve(
            MAINTAINER,
            task.id,
            plan_version=1,
            task_version=task.version,
            rank=None,
            allow_test_changes=allow,
        )

    async def plan(self, task: TaskRecord) -> PlanRecord:
        plan = await self.store.latest_plan(MAINTAINER.user_id, task.id)
        assert plan is not None
        return plan


async def test_test_changes_need_allow_test_changes() -> None:
    gate = Gate()
    task = await gate.card((), ("backend/tests/unit/test_x.py",))
    with pytest.raises(ValidationFailed) as caught:
        await gate.approve(task, allow=False)
    assert "allow_test_changes" in (caught.value.detail or "")
    assert (await gate.plan(task)).plan_approved_at is None


async def test_allowing_test_changes_adds_one_combined_label() -> None:
    gate = Gate()
    paths = ("backend/tests/unit/test_x.py", "web/tests/a.spec.ts")
    task = await gate.card(("ux",), paths)
    approved = await gate.approve(task, allow=True)
    assert approved.column is Column.READY
    assert approved.labels == ("ux", "modify-tests:" + ",".join(paths))
    assert (await gate.plan(task)).is_approved


async def test_labels_already_covering_the_tests_need_no_allowance() -> None:
    gate = Gate()
    task = await gate.card(("modify-tests:backend/tests/unit/",), ("backend/tests/unit/test_x.py",))
    approved = await gate.approve(task, allow=False)
    assert approved.column is Column.READY
    assert approved.labels == ("modify-tests:backend/tests/unit/",)


async def test_a_combined_label_too_long_splits_into_one_label_per_path() -> None:
    gate = Gate()
    paths = tuple(f"backend/tests/unit/devboard/test_long_module_name_{i}.py" for i in range(3))
    task = await gate.card((), paths)
    approved = await gate.approve(task, allow=True)
    assert approved.labels == tuple(f"modify-tests:{path}" for path in paths)
    assert all(len(label) <= MAX_LABEL_CHARS for label in approved.labels)


async def test_labels_that_cannot_fit_refuse_the_approval() -> None:
    gate = Gate()
    labels = tuple(f"tag-{i}" for i in range(MAX_LABELS - 1))
    paths = tuple(f"backend/tests/unit/devboard/test_long_module_name_{i}.py" for i in range(3))
    task = await gate.card(labels, paths)
    with pytest.raises(ValidationFailed) as caught:
        await gate.approve(task, allow=True)
    assert "label" in (caught.value.detail or "")
    assert (await gate.plan(task)).plan_approved_at is None


async def test_a_path_too_long_for_any_label_refuses_the_approval() -> None:
    gate = Gate()
    path = "backend/tests/unit/" + "d" * 80 + "/test_x.py"
    task = await gate.card((), (path,))
    with pytest.raises(ValidationFailed):
        await gate.approve(task, allow=True)
