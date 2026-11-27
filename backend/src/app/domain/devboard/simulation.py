"""A simulated `AgentRunner` so the board, the run tree and the step stream work end to end
offline. It follows the real flow's shape (read-only plan, test-only step, leases, handoff)
without touching the repo. The sandboxed runner replaces it in M2 behind the same port."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from app.domain.devboard.model import (
    Area,
    Criterion,
    NewStep,
    Risk,
    RunKind,
    Size,
    StepKind,
    Subtask,
    TaskRecord,
    TriageRecord,
)
from app.domain.devboard.proposals import split_body
from app.domain.devboard.triage_policy import AREA_DEFAULT_PATHS
from app.domain.ports.devboard import AgentResult, PlanDraft, RunContext, RunTools

type Sleep = Callable[[float], Awaitable[None]]

BUDGETS: dict[Size, tuple[float, int]] = {Size.S: (2.0, 30), Size.M: (5.0, 60), Size.L: (10.0, 120)}
TEST_PATHS: dict[Area, str] = {Area.API: "backend/tests/unit/", Area.WEB: "web/tests/"}
ENGINEERS: dict[Area, str] = {Area.API: "backend-engineer", Area.WEB: "frontend-engineer"}
CONTRACT = "api/openapi.yaml"
# Where the simulated planner puts the acceptance tests (directories that exist in the repo).
CRITERIA_FILES: dict[Area, str] = {
    Area.API: "backend/tests/unit/test_acceptance.py",
    Area.WEB: "web/tests/e2e/acceptance.spec.ts",
}
MAX_CRITERIA = 30
_NOT_WORD = re.compile(r"[^a-z0-9]+")


def _test_name(text: str, index: int) -> str:
    slug = _NOT_WORD.sub("_", text.lower()).strip("_")[:60].rstrip("_")
    return f"test_{index}_{slug}" if slug else f"test_{index}"


def draft_criteria(task: TaskRecord, area: Area) -> tuple[Criterion, ...]:
    """Triage writes no criteria, so the planner does: one per written criterion on the card
    (`## Acceptance criteria` bullets), or one for the title when the card has none."""
    _, written = split_body(task.body)
    expectations = written[:MAX_CRITERIA] or (f"{task.title.strip()} works as described.",)
    test_file = CRITERIA_FILES[area]
    return tuple(
        Criterion(f"{test_file}::{_test_name(text, index)}", text[:1000])
        for index, text in enumerate(expectations, start=1)
    )


def draft_plan(task: TaskRecord, triage: TriageRecord | None) -> PlanDraft:
    """agents.md policy: single agent for one area, size S, low risk; otherwise a team led
    by the test-first `qa-expert` subtask."""
    areas = triage.areas if triage and triage.areas else (Area.API,)
    size = triage.size if triage else Size.M
    risk = triage.risk if triage else Risk.MEDIUM
    is_single = len(areas) == 1 and size is Size.S and risk is Risk.LOW
    criteria = draft_criteria(task, areas[0])
    subtasks: list[Subtask] = [
        Subtask(
            id="t0",
            title="Write the failing acceptance tests",
            agent="qa-expert",
            lease_paths=tuple(TEST_PATHS[area] for area in areas),
            is_test_only=True,
        )
    ]
    depends: tuple[str, ...] = ("t0",)
    if not is_single and Area.API in areas:
        subtasks.append(Subtask("c1", "Contract change", "backend-engineer", (CONTRACT,), depends))
        depends = ("c1",)
    for index, area in enumerate(areas, start=1):
        subtasks.append(
            Subtask(
                id=f"s{index}",
                title=f"Implement the {area.value} change",
                agent=ENGINEERS[area],
                lease_paths=AREA_DEFAULT_PATHS[area],
                depends_on=depends,
            )
        )
    budget_usd, minutes = BUDGETS[size]
    return PlanDraft(
        mode="single" if is_single else "multi",
        subtasks=tuple(subtasks),
        acceptance_tests=tuple(criterion.test_id for criterion in criteria),
        budget_usd=budget_usd,
        budget_minutes=minutes,
        llm_risk=risk,
        acceptance_criteria=criteria,
    )


class SimulatedAgentRunner:
    def __init__(self, sleep: Sleep, step_delay: float = 0.4) -> None:
        self._sleep = sleep
        self._delay = step_delay

    async def run(self, context: RunContext, tools: RunTools) -> AgentResult:
        if context.run.kind is RunKind.PLAN:
            return await self._plan(context, tools)
        return await self._develop(context, tools)

    async def _say(self, tools: RunTools, step: NewStep) -> bool:
        """Emit one step after a pause; False once the run was killed."""
        await self._sleep(self._delay)
        if await tools.is_cancelled():
            return False
        await tools.emit(step)
        return True

    async def _plan(self, context: RunContext, tools: RunTools) -> AgentResult:
        draft = draft_plan(context.task, context.triage)
        script = (
            NewStep(StepKind.STATUS, "Dev-lead started (read-only).", "dev-lead"),
            NewStep(
                StepKind.TOOL_CALL,
                "Read the card, its triage and docs/agents.md.",
                "dev-lead",
                "read",
            ),
            NewStep(
                StepKind.THOUGHT, f"Mode: {draft.mode}; {len(draft.subtasks)} subtasks.", "dev-lead"
            ),
            NewStep(
                StepKind.TOOL_CALL, "Write the failing acceptance tests.", "qa-expert", "write"
            ),
            NewStep(
                StepKind.TOOL_RESULT,
                f"{len(draft.acceptance_tests)} tests fail on the base.",
                "qa-expert",
                "pytest",
            ),
            NewStep(StepKind.HANDOFF, "Plan and frozen tests ready for Plan review.", "dev-lead"),
        )
        for step in script:
            if not await self._say(tools, step):
                return AgentResult(is_success=False, outcome="Stopped.")
        return AgentResult(
            is_success=True, outcome="Plan ready for review.", model="simulated", plan=draft
        )

    async def _develop(self, context: RunContext, tools: RunTools) -> AgentResult:
        plan = context.plan
        if plan is None or not plan.is_approved:
            return AgentResult(is_success=False, outcome="No approved plan.")
        paths = tuple(p for s in plan.subtasks if not s.is_test_only for p in s.lease_paths)
        claim = await tools.claim_paths(paths)
        if claim.conflicts:
            busy = ", ".join(sorted({lease.path for lease in claim.conflicts}))
            return AgentResult(is_success=False, outcome=f"Lease wait: {busy} is busy.")
        granted = ", ".join(lease.path for lease in claim.granted)
        script = (
            NewStep(StepKind.LEASE, f"Leased {granted}.", "dev-lead", "claim_paths"),
            NewStep(StepKind.TOOL_CALL, "Edit the files in the lease.", "backend-engineer", "edit"),
            NewStep(StepKind.TOOL_CALL, "uv run pytest -q --tb=short", "backend-engineer", "bash"),
            NewStep(
                StepKind.TOOL_RESULT,
                f"{len(plan.acceptance_tests)} acceptance tests pass.",
                "backend-engineer",
                "bash",
            ),
            NewStep(StepKind.HANDOFF, "status: done; bundle ready for the Publisher.", "dev-lead"),
        )
        for step in script:
            if not await self._say(tools, step):
                return AgentResult(is_success=False, outcome="Stopped.")
            await tools.renew_leases()
        return AgentResult(is_success=True, outcome="Bundle ready (simulated).", model="simulated")
