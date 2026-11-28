"""A `Workflow` on in-memory fakes with a scripted triage agent, a scripted runner (plans, dev
runs that publish a branch, scripted reviews) and a fixed repo listing (no LLM, no replay)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace

from app.adapters.fakes.data import InMemoryNotificationHub
from app.adapters.fakes.devboard import (
    InMemoryLeaseStore,
    InMemoryRunStore,
    InMemoryTaskStore,
    InProcessDispatcher,
)
from app.adapters.fakes.devboard_reviews import FakeBranchViews, FixedRubricIds
from app.adapters.fakes.security import FakePII
from app.adapters.fakes.system import FakeClock, SequentialIds
from app.domain.devboard.board import BoardService
from app.domain.devboard.leases import LeaseService
from app.domain.devboard.model import (
    Actor,
    ActorKind,
    Area,
    Column,
    Criterion,
    NewStep,
    Priority,
    ReviewFinding,
    ReviewSeverity,
    Risk,
    Route,
    RunKind,
    RunRecord,
    Severity,
    Size,
    StepKind,
    Subtask,
    TaskRecord,
    TaskType,
    Verification,
    VerificationStatus,
)
from app.domain.devboard.plans import PlanService
from app.domain.devboard.review_flow import ReviewInputs
from app.domain.devboard.runs import RunService
from app.domain.devboard.workflow import Agents, Stores, Workflow
from app.domain.identity import AppRole
from app.domain.ports.devboard import (
    AgentResult,
    AgentRunner,
    BranchDiff,
    PlanDraft,
    ReviewDraft,
    RunContext,
    RunTools,
    TriageDecision,
)

MAINTAINER = Actor(uuid.UUID(int=1), ActorKind.MAINTAINER, AppRole.MAINTAINER)
USER = Actor(uuid.UUID(int=2), ActorKind.USER, AppRole.USER)
FILES = frozenset(
    {
        "backend/src/app/api/devboard/tasks.py",
        "backend/src/app/domain/devboard/board.py",
        "backend/tests/unit/devboard/test_board_api.py",
        "web/src/features/devboard/Board.tsx",
        "web/tests/e2e/devboard-board.spec.ts",
        "api/openapi.yaml",
    }
)


def decision(**changes: object) -> TriageDecision:
    base = TriageDecision(
        type=TaskType.BUG,
        size=Size.S,
        llm_risk=Risk.LOW,
        areas=(Area.WEB,),
        priority=Priority.P2,
        severity=Severity.MINOR,
        impact="Board users lose a card after a conflict.",
        route=Route.NEXT,
        route_reason="Contained; do it soon.",
        verification=Verification(
            VerificationStatus.CONFIRMED,
            "The move handler drops the card on error.",
            ("web/src/features/devboard/Board.tsx",),
        ),
        needs_human_design=False,
        likely_paths=("web/src/features/devboard/",),
    )
    return replace(base, **changes)


def draft(**changes: object) -> PlanDraft:
    base = PlanDraft(
        mode="single",
        subtasks=(
            Subtask("t0", "Tests", "qa-expert", ("web/tests/e2e/",), is_test_only=True),
            Subtask("s1", "Fix", "frontend-engineer", ("web/src/features/devboard/",), ("t0",)),
        ),
        acceptance_tests=("web/tests/e2e/devboard-board.spec.ts::keeps the card",),
        budget_usd=1.0,
        budget_minutes=30,
        llm_risk=Risk.LOW,
        acceptance_criteria=(
            Criterion("web/tests/e2e/devboard-board.spec.ts::keeps the card", "card stays"),
        ),
    )
    return replace(base, **changes)


class StaticFiles:
    def __init__(self, files: frozenset[str] = FILES) -> None:
        self.files = files

    async def paths(self) -> frozenset[str]:
        return self.files


@dataclass
class ScriptedTriage:
    """Answers each card by title (default `decision()`), and keeps every context it saw."""

    answers: dict[str, TriageDecision] = field(default_factory=dict[str, TriageDecision])
    seen: list[RunContext] = field(default_factory=list[RunContext])

    async def triage(self, context: RunContext, tools: RunTools) -> TriageDecision:
        self.seen.append(context)
        await tools.emit(NewStep(StepKind.STATUS, "Scripted triage.", "triage"))
        return self.answers.get(context.task.title, decision())


BASE_SHA = "a" * 40
CHANGED = "web/src/features/devboard/Board.tsx"
RUBRIC = frozenset({"ARCH-03", "TEST-01"})
PATCH = "From 0000 Mon Sep 17 00:00:00 2001\nSubject: [PATCH] Fix the board\n"


def branch_name(run: RunRecord) -> str:
    return f"agent/{str(run.task_id)[:8]}-{str(run.id)[:8]}"


def unified_diff(*paths: str) -> str:
    lines = ("diff --git a/{0} b/{0}", "--- a/{0}", "+++ b/{0}", "@@ -1 +1 @@", "-old", "+new")
    return "".join("".join(line.format(path) + "\n" for line in lines) for path in paths)


@dataclass
class ScriptedRunner:
    """Plans; dev runs publish a branch (to `branches`, changing `CHANGED`); review runs answer
    from `reviews` in order (default: a clean review). Keeps every context it saw."""

    plan: PlanDraft = field(default_factory=draft)
    runs: list[RunKind] = field(default_factory=list[RunKind])
    reviews: list[AgentResult] = field(default_factory=list[AgentResult])
    seen: list[RunContext] = field(default_factory=list[RunContext])
    branches: FakeBranchViews = field(default_factory=FakeBranchViews)

    async def run(self, context: RunContext, tools: RunTools) -> AgentResult:
        self.runs.append(context.run.kind)
        self.seen.append(context)
        await tools.emit(NewStep(StepKind.STATUS, "Scripted run.", "dev-lead"))
        if context.run.kind is RunKind.REVIEW:
            return self.reviews.pop(0) if self.reviews else reviewed()
        if context.run.kind is RunKind.DEV:
            branch = branch_name(context.run)
            text = unified_diff(CHANGED)
            self.branches.publish(BranchDiff(branch, BASE_SHA, 1, 1, 1, text), PATCH)
            return AgentResult(
                is_success=True,
                outcome="Bundle ready.",
                cost_usd=1.0,
                branch=branch,
                base_sha=BASE_SHA,
            )
        return AgentResult(is_success=True, outcome="Plan ready.", plan=self.plan, cost_usd=0.5)


def reviewed(*findings: ReviewFinding, cost_usd: float = 0.75) -> AgentResult:
    return AgentResult(
        is_success=True,
        outcome="Reviewed.",
        cost_usd=cost_usd,
        model="claude-opus",
        review=ReviewDraft(summary="Looked at the change.", findings=findings),
    )


def blocker(**changes: object) -> ReviewFinding:
    base = ReviewFinding(
        ReviewSeverity.BLOCKER, path=CHANGED, evidence="Route holds a rule.", rubric_id="ARCH-03"
    )
    return replace(base, **changes)


class Harness:
    def __init__(
        self,
        files: frozenset[str] = FILES,
        runner: AgentRunner | None = None,
        *,
        rubric: frozenset[str] = RUBRIC,
    ) -> None:
        clock, ids = FakeClock(), SequentialIds()
        self.runs = InMemoryRunStore(ids)
        self.tasks = InMemoryTaskStore(self.runs)
        self.board = BoardService(self.tasks, FakePII(), clock, ids)
        hub = InMemoryNotificationHub()
        self.run_service = RunService(self.runs, self.tasks, hub, _Plain(), clock)
        self.plans = PlanService(self.tasks, self.board, clock, ids)
        leases = LeaseService(InMemoryLeaseStore(ids), clock)
        self.triage = ScriptedTriage()
        self.runner = ScriptedRunner()
        self.files = StaticFiles(files)
        self.dispatcher = InProcessDispatcher()
        self.workflow = Workflow(
            Stores(tasks=self.tasks, runs=self.runs),
            (self.board, self.run_service, self.plans, leases),
            Agents(triage=self.triage, runner=runner or self.runner),
            self.dispatcher,
            (clock, ids),
            repo_files=self.files,
            review=ReviewInputs(self.runner.branches, FixedRubricIds(rubric)),
        )
        self.board.attach(self.workflow)

    async def create(self, title: str, actor: Actor = MAINTAINER) -> TaskRecord:
        task = await self.board.create(actor, title, "", ())
        await self.dispatcher.drain()
        return await self.board.get(MAINTAINER, task.id)

    async def start_dev(self, title: str = "Fix the board") -> TaskRecord:
        """A card dropped into `agent_dev` (plan approval is not the point here)."""
        task = await self.create(title)
        dev = await self.tasks.save(
            MAINTAINER.user_id, replace(task, column=Column.AGENT_DEV), task.version
        )
        await self.workflow.on_moved(MAINTAINER, dev, Column.READY)
        await self.dispatcher.drain()
        return await self.board.get(MAINTAINER, task.id)

    async def kinds(self, task: TaskRecord) -> list[RunKind]:
        """Run kinds, oldest first."""
        runs = await self.runs.for_task(MAINTAINER.user_id, task.id, 0, 50)
        return [run.kind for run in reversed(runs)]

    async def latest_run(self, task: TaskRecord, kind: RunKind) -> RunRecord:
        runs = await self.runs.for_task(MAINTAINER.user_id, task.id, 0, 20)
        return next(run for run in runs if run.kind is kind)


class _Plain:
    async def redact(self, text: str) -> str:
        return text
