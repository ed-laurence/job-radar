"""Work plans and the Plan review gate.

The acceptance tests are frozen by the test-only step and a human approves them together
with the plan; only then can the card reach `ready`. Plan risk comes from the path-risk map
over every subtask's lease paths, and the planner's own estimate can only raise it.

Code decides the plan's shape, not the model (`finalise_draft`): a subtask that leases the
contract also leases the code regenerated from it, the test-only subtask also gets the contract
tests, and a draft whose acceptance tests or test changes aren't usable test files is refused.
Existing tests are read-only unless a `modify-tests:` label covers them; approving a plan that
changes uncovered tests needs `allow_test_changes`, which adds the label(s).
"""

from __future__ import annotations

import uuid
from dataclasses import replace

from app.domain.devboard.board import MAX_LABEL_CHARS, MAX_LABELS, MODIFY_TESTS, BoardService
from app.domain.devboard.model import (
    Actor,
    Column,
    EventKind,
    PlanRecord,
    Risk,
    Subtask,
    TaskEdit,
    TaskRecord,
    max_risk,
)
from app.domain.devboard.paths import (
    CONTRACT,
    REGENERATED,
    InvalidPath,
    code_risk,
    contains,
    is_regenerated,
    is_test_file,
    normalise,
)
from app.domain.devboard.repo_paths import criterion_file
from app.domain.errors import (
    Conflict,
    IllegalTransition,
    NotFound,
    PlanNotApproved,
    ValidationFailed,
)
from app.domain.ports import Clock, Ids
from app.domain.ports.devboard import PlanDraft, TaskStore

MAX_LEASE_PATHS = 50
MAX_CRITERIA = 30
CONTRACT_TESTS = "backend/tests/contract/"
_SHOWN_CHARS = 80


def plan_risk(draft: PlanDraft) -> Risk:
    paths: list[str] = []
    for subtask in draft.subtasks:
        for raw in subtask.lease_paths:
            try:
                paths.append(normalise(raw))
            except InvalidPath:
                # An unusable lease path can't be judged safe.
                return Risk.HIGH
    return max_risk(code_risk(tuple(paths)), draft.llm_risk)


# ------------------------------------------------------------------ drafts
def _shown(text: str) -> str:
    """Untrusted model text as one short line for a run outcome."""
    return " ".join(text.split())[:_SHOWN_CHARS]


def _clean(raw: str) -> str | None:
    try:
        return normalise(raw)
    except InvalidPath:
        return None


def _covers(leases: tuple[str, ...], path: str) -> bool:
    return any(clean is not None and contains(clean, path) for clean in map(_clean, leases))


def _check_acceptance_tests(tests: tuple[str, ...], what: str = "acceptance test") -> None:
    for test_id in tests:
        path, sep, name = test_id.partition("::")
        clean = _clean(path)
        if not sep or not name.strip() or clean is None or not is_test_file(clean):
            raise ValidationFailed(
                f"The plan's {what} {_shown(test_id)!r} is not in a writable test file."
            )


def plan_paths(draft: PlanDraft) -> tuple[str, ...]:
    """The repo paths a draft names that must exist (`repo_paths.unknown_paths`): every
    acceptance criterion's test file and every lease path. Regenerated code is left out: code
    adds it to contract leases, and the generator, not the planner, writes it."""
    criteria = (criterion_file(criterion.test_id) for criterion in draft.acceptance_criteria)
    leases = (path for subtask in draft.subtasks for path in subtask.lease_paths)
    named = (path for path in (*criteria, *leases) if not _is_regenerated(path))
    return tuple(dict.fromkeys(named))


def _is_regenerated(raw: str) -> bool:
    clean = _clean(raw)
    return clean is not None and is_regenerated(clean)


def _tests_to_change(paths: tuple[str, ...]) -> tuple[str, ...]:
    clean: list[str] = []
    for raw in paths:
        path = _clean(raw)
        if path is None or not is_test_file(path):
            raise ValidationFailed(
                f"The plan's test change {_shown(raw)!r} is not an existing test file"
                " in the test dirs."
            )
        clean.append(path)
    return tuple(dict.fromkeys(clean))


def _with_leases(subtask: Subtask, extra: tuple[str, ...]) -> Subtask:
    missing = tuple(path for path in extra if not _covers(subtask.lease_paths, path))
    if not missing:
        return subtask
    return replace(subtask, lease_paths=(*subtask.lease_paths, *missing))


def _contract_leases(subtasks: tuple[Subtask, ...]) -> tuple[Subtask, ...]:
    if not any(_covers(s.lease_paths, CONTRACT) for s in subtasks):
        return subtasks
    changed: list[Subtask] = []
    for subtask in subtasks:
        current = subtask
        if _covers(current.lease_paths, CONTRACT):
            current = _with_leases(current, REGENERATED)
        if current.is_test_only:
            current = _with_leases(current, (CONTRACT_TESTS,))
        changed.append(current)
    return tuple(changed)


def finalise_draft(draft: PlanDraft) -> PlanDraft:
    """The draft as it is stored. Raises `ValidationFailed` (one line) to refuse it."""
    _check_acceptance_tests(draft.acceptance_tests)
    if len(draft.acceptance_criteria) > MAX_CRITERIA:
        raise ValidationFailed(f"The plan has more than {MAX_CRITERIA} acceptance criteria.")
    criteria = tuple(criterion.test_id for criterion in draft.acceptance_criteria)
    _check_acceptance_tests(criteria, "acceptance criterion")
    to_change = _tests_to_change(draft.tests_to_change)
    subtasks = _contract_leases(draft.subtasks)
    for subtask in subtasks:
        if len(subtask.lease_paths) > MAX_LEASE_PATHS:
            raise ValidationFailed(
                f"The plan's subtask {_shown(subtask.id)!r} leases more than"
                f" {MAX_LEASE_PATHS} paths."
            )
    return replace(draft, subtasks=subtasks, tests_to_change=to_change)


# ------------------------------------------------------------------ test changes
def _label_path(path: str) -> str:
    """The web's rule (`testChanges.ts`): trim, drop a leading `./` and trailing `/`s."""
    return path.strip().removeprefix("./").rstrip("/")


def _granted(labels: tuple[str, ...]) -> list[str]:
    paths = (
        _label_path(part)
        for label in labels
        if label.startswith(MODIFY_TESTS)
        for part in label[len(MODIFY_TESTS) :].split(",")
    )
    return [path for path in paths if path]


def uncovered_tests(paths: tuple[str, ...], labels: tuple[str, ...]) -> tuple[str, ...]:
    """`paths` no `modify-tests:` label covers. An entry covers itself and, as a folder, every
    path under it; a partial folder name doesn't count."""
    granted = _granted(labels)

    def is_covered(path: str) -> bool:
        clean = _label_path(path)
        return any(clean == entry or clean.startswith(f"{entry}/") for entry in granted)

    return tuple(path for path in paths if not is_covered(path))


def labels_for_test_changes(paths: tuple[str, ...], existing: tuple[str, ...]) -> tuple[str, ...]:
    """One combined `modify-tests:` label, or one per path when that won't fit the limits."""
    combined = MODIFY_TESTS + ",".join(paths)
    if len(combined) <= MAX_LABEL_CHARS:
        labels: tuple[str, ...] = (combined,)
    else:
        labels = tuple(MODIFY_TESTS + path for path in paths)
    too_long = next((label for label in labels if len(label) > MAX_LABEL_CHARS), None)
    if too_long is not None:
        raise ValidationFailed(
            f"The test path {_shown(too_long[len(MODIFY_TESTS) :])!r} is too long for a"
            f" modify-tests label ({MAX_LABEL_CHARS} characters); add a covering label first."
        )
    if len(existing) + len(labels) > MAX_LABELS:
        raise ValidationFailed(
            f"The card has no room for {len(labels)} more modify-tests label(s)"
            f" (at most {MAX_LABELS} labels); add a covering label first."
        )
    return labels


class PlanService:
    def __init__(self, store: TaskStore, board: BoardService, clock: Clock, ids: Ids) -> None:
        self._store = store
        self._board = board
        self._clock = clock
        self._ids = ids

    async def get(self, actor: Actor, task_id: uuid.UUID) -> PlanRecord:
        await self._board.get(actor, task_id)
        plan = await self._store.latest_plan(actor.user_id, task_id)
        if plan is None:
            raise NotFound("This card has no plan yet.")
        return plan

    async def latest(self, actor: Actor, task_id: uuid.UUID) -> PlanRecord | None:
        await self._board.get(actor, task_id)
        return await self._store.latest_plan(actor.user_id, task_id)

    async def save_draft(self, actor: Actor, task: TaskRecord, draft: PlanDraft) -> PlanRecord:
        """Raises `ValidationFailed` when the draft is refused (`finalise_draft`)."""
        final = finalise_draft(draft)
        previous = await self._store.latest_plan(actor.user_id, task.id)
        plan = PlanRecord(
            id=self._ids.new_id(),
            task_id=task.id,
            version=1 if previous is None else previous.version + 1,
            mode=final.mode,
            subtasks=final.subtasks,
            acceptance_tests=final.acceptance_tests,
            tests_to_change=final.tests_to_change,
            acceptance_criteria=final.acceptance_criteria,
            budget_usd=final.budget_usd,
            budget_minutes=final.budget_minutes,
            risk=plan_risk(final),
            created_at=self._clock.now(),
        )
        await self._store.add_plan(actor.user_id, plan)
        await self._board.record(actor, task, EventKind.PLANNED)
        return plan

    async def withdraw_approval(self, actor: Actor, task_id: uuid.UUID) -> None:
        """The card's text changed after approval: tests and plan must be approved again."""
        plan = await self._store.latest_plan(actor.user_id, task_id)
        if plan is None or (plan.tests_approved_at is None and plan.plan_approved_at is None):
            return
        withdrawn = replace(plan, tests_approved_at=None, plan_approved_at=None, approved_by=None)
        await self._store.save_plan(actor.user_id, withdrawn, plan.version)

    async def approve(
        self,
        actor: Actor,
        task_id: uuid.UUID,
        *,
        plan_version: int,
        task_version: int,
        rank: str | None,
        allow_test_changes: bool = False,
    ) -> TaskRecord:
        task = await self._board.get(actor, task_id)
        if task.column is not Column.PLAN_REVIEW:
            raise IllegalTransition(
                "Only cards in Plan review can be approved.", allowed=(task.column.value,)
            )
        plan = await self._store.latest_plan(actor.user_id, task_id)
        if plan is None:
            raise PlanNotApproved("There is no plan to approve yet.")
        if task.version != task_version:
            raise Conflict("The card changed elsewhere.", current_version=task.version)
        if plan.version != plan_version:
            raise Conflict("The plan changed elsewhere.", current_version=plan.version)
        await self._allow_test_changes(actor, task, plan, allow=allow_test_changes)
        now = self._clock.now()
        approved = replace(
            plan, tests_approved_at=now, plan_approved_at=now, approved_by=actor.user_id
        )
        await self._store.save_plan(actor.user_id, approved, plan_version)
        moved = await self._board.system_move(actor, task_id, Column.READY, rank=rank)
        await self._board.record(actor, moved, EventKind.APPROVED)
        return moved

    async def _allow_test_changes(
        self, actor: Actor, task: TaskRecord, plan: PlanRecord, *, allow: bool
    ) -> None:
        """Existing tests the plan changes need a covering `modify-tests:` label."""
        uncovered = uncovered_tests(plan.tests_to_change, task.labels)
        if not uncovered:
            return
        if not allow:
            raise ValidationFailed(
                f"The plan changes existing tests ({_shown(', '.join(uncovered))}); approve"
                " with allow_test_changes to add the modify-tests label."
            )
        labels = (*task.labels, *labels_for_test_changes(uncovered, task.labels))
        await self._board.update(actor, task.id, TaskEdit(version=task.version, labels=labels))
