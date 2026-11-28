"""Read the sandboxed dev-lead's `/out/plan.json` into a `PlanDraft`.

The file is untrusted output of agent-controlled code: it is size-capped, strictly typed
(unknown keys refused, no type coercion), and must name the run's own task. Anything else is
`PlanFileError`, whose fixed message is safe to show. Whether the paths are usable is the
domain's call (`app.domain.devboard.plans.finalise_draft`), not this parser's.

Contract (`sandbox/writer/plan_contract.py`): {"task_id", "mode": "single"|"multi",
"subtasks": [{"id", "title", "agent", "lease_paths", "depends_on", "test_only"}] (1..20),
"acceptance_criteria": [{"test_id": "<file>::<name>", "expectation"}] (1..30),
"tests_to_change" (<=50), "budget_usd" (0..20), "budget_minutes" (1..120),
"risk": "low"|"medium"|"high"}. The planner writes the criteria (triage doesn't); the draft's
acceptance tests are their test ids, once each.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from app.domain.devboard.model import Criterion, Risk, Subtask
from app.domain.ports.devboard import PlanDraft

MAX_PLAN_BYTES = 64 * 1024
MALFORMED = "The dev-lead's plan is malformed."

type _Id = Annotated[str, StringConstraints(min_length=1, max_length=64)]
type _Item = Annotated[str, StringConstraints(min_length=1, max_length=300)]


class PlanFileError(Exception):
    """The plan file is missing or malformed. The message is safe to show."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PlanFileSubtask(_Strict):
    id: _Id
    title: str = Field(min_length=1, max_length=200)
    agent: _Id
    lease_paths: list[_Item] = Field(max_length=50)
    depends_on: list[_Id] = Field(max_length=20)
    test_only: bool


class PlanFileCriterion(_Strict):
    test_id: _Item = Field(pattern=r"^[^\n]*[^\s:][^\n]*::[^\n]*\S[^\n]*$")
    expectation: str = Field(min_length=1, max_length=1000)


class PlanFile(_Strict):
    task_id: str = Field(max_length=64)
    mode: Literal["single", "multi"]
    subtasks: list[PlanFileSubtask] = Field(min_length=1, max_length=20)
    acceptance_criteria: list[PlanFileCriterion] = Field(min_length=1, max_length=30)
    tests_to_change: list[_Item] = Field(max_length=50)
    budget_usd: float = Field(ge=0, le=20)
    budget_minutes: int = Field(ge=1, le=120)
    risk: Literal["low", "medium", "high"]


def _is_consistent(plan: PlanFile) -> bool:
    """Subtask ids are unique and every dependency names one of them."""
    ids = [subtask.id for subtask in plan.subtasks]
    known = set(ids)
    return len(known) == len(ids) and all(
        dep in known for subtask in plan.subtasks for dep in subtask.depends_on
    )


def _draft(plan: PlanFile) -> PlanDraft:
    return PlanDraft(
        mode=plan.mode,
        subtasks=tuple(
            Subtask(
                id=s.id,
                title=s.title,
                agent=s.agent,
                lease_paths=tuple(s.lease_paths),
                depends_on=tuple(s.depends_on),
                is_test_only=s.test_only,
            )
            for s in plan.subtasks
        ),
        acceptance_tests=tuple(dict.fromkeys(c.test_id for c in plan.acceptance_criteria)),
        budget_usd=float(plan.budget_usd),
        budget_minutes=plan.budget_minutes,
        llm_risk=Risk(plan.risk),
        tests_to_change=tuple(plan.tests_to_change),
        acceptance_criteria=tuple(
            Criterion(c.test_id, c.expectation) for c in plan.acceptance_criteria
        ),
    )


def parse_plan(raw: bytes, task_id: str) -> PlanDraft:
    if len(raw) > MAX_PLAN_BYTES:
        raise PlanFileError("The dev-lead's plan is too large.")
    try:
        plan = PlanFile.model_validate_json(raw)
    except ValidationError:
        raise PlanFileError(MALFORMED) from None
    if plan.task_id != task_id:
        raise PlanFileError("The dev-lead's plan names another task.")
    if not _is_consistent(plan):
        raise PlanFileError(MALFORMED)
    return _draft(plan)


def read_plan(path: Path, task_id: str) -> PlanDraft:
    """Blocking (run it in a thread)."""
    if path.is_symlink():
        # The agent controls /out; a link could point anywhere on the host.
        raise PlanFileError("The dev-lead's plan is not a regular file.")
    try:
        if not path.is_file():
            raise PlanFileError("The dev-lead wrote no plan.")
        if path.stat().st_size > MAX_PLAN_BYTES:
            raise PlanFileError("The dev-lead's plan is too large.")
        raw = path.read_bytes()
    except FileNotFoundError:
        raise PlanFileError("The dev-lead wrote no plan.") from None
    return parse_plan(raw, task_id)
