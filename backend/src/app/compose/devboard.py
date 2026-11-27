"""Dev-board area: stores, agents, services and routes, built from the `Container`.

Postgres stores when the container has an engine, in-memory fakes otherwise. Step streams use
the container's shared LISTEN hub (ARCH-31), or an in-memory hub offline.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.agents.docker_runner import DockerAgentRunner, SandboxConfig, SandboxDeps
from app.adapters.agents.docker_triage import DockerTriageAgent
from app.adapters.agents.process import AsyncioProcessRunner
from app.adapters.agents.tokens import CoordinatorTokenMint
from app.adapters.agents.usage import DockerUsageProbe
from app.adapters.codehost.local_git import LocalGitHost
from app.adapters.codehost.working_tree import ListedRepoFiles, WorkingTreeFiles
from app.adapters.db.devboard_leases import PgLeaseStore
from app.adapters.db.devboard_runs import PgRunStore
from app.adapters.db.devboard_tasks import PgTaskStore
from app.adapters.db.engine import session_factory, system_transaction
from app.adapters.db.task_chat import PgTaskChats
from app.adapters.fakes.devboard import (
    InMemoryLeaseStore,
    InMemoryRunStore,
    InMemoryTaskStore,
    InProcessDispatcher,
)
from app.adapters.fakes.task_chat import MemoryTaskChats
from app.agents.task_chat.agent import TaskChatLlmAgent
from app.agents.triage.agent import TriageLlmAgent
from app.api.devboard import chat as chat_routes
from app.api.devboard import runs as run_routes
from app.api.devboard import tasks as task_routes
from app.api.devboard.deps import DevboardServices
from app.compose.jobs import GuardRedaction
from app.domain.devboard.board import BoardService
from app.domain.devboard.leases import LeaseService
from app.domain.devboard.model import (
    Actor,
    ActorKind,
    NewStep,
    RunKind,
    RunRecord,
    StepRecord,
)
from app.domain.devboard.plans import PlanService
from app.domain.devboard.runs import RunService
from app.domain.devboard.schedule import JobName, JobReport, JobRunLog
from app.domain.devboard.simulation import SimulatedAgentRunner
from app.domain.devboard.task_chat import TaskChatService
from app.domain.devboard.task_chat_decisions import ProposalDecisions
from app.domain.devboard.workflow import Agents, Stores, Workflow
from app.domain.identity import AppRole
from app.domain.ports import LLM, NotificationHub
from app.domain.ports.devboard import (
    AgentRunner,
    LeaseStore,
    RepoFiles,
    RunFinish,
    RunStore,
    TaskStore,
    TextRedactor,
    TriageAgent,
)
from app.domain.ports.task_chat import TaskChatStore
from app.guards.redaction import Redactor
from app.settings import REPO_ROOT

if TYPE_CHECKING:
    from app.main import Container

type Sleep = Callable[[float], Awaitable[None]]

# Pause between simulated agent steps, so the live stream is visible in the UI.
STEP_DELAY_SECONDS = 0.6
# Kept logs of sandboxed runs (gitignored with the rest of .artifacts/).
AGENT_ARTIFACTS = REPO_ROOT / ".artifacts" / "agent-runs"


@dataclass(frozen=True)
class BoardStores:
    tasks: TaskStore
    runs: RunStore
    leases: LeaseStore
    hub: NotificationHub


@dataclass(frozen=True)
class DevboardRuntime:
    services: DevboardServices
    stores: BoardStores
    leases: LeaseService
    dispatcher: InProcessDispatcher


def board_stores(container: Container) -> BoardStores:
    if container.engine is not None and container.hub is not None:
        sessions = session_factory(container.engine)
        return BoardStores(
            tasks=PgTaskStore(sessions),
            runs=PgRunStore(sessions),
            leases=PgLeaseStore(sessions),
            hub=container.hub,
        )
    hub = container.offline_hub
    runs = InMemoryRunStore(container.ids, publisher=hub)
    return BoardStores(
        tasks=InMemoryTaskStore(runs), runs=runs, leases=InMemoryLeaseStore(container.ids), hub=hub
    )


def task_chat_store(container: Container) -> TaskChatStore:
    if container.engine is not None:
        return PgTaskChats(session_factory(container.engine))
    return MemoryTaskChats()


def devboard_llm(container: Container) -> LLM:
    """The shared LLM adapter. Under replay it also finds the board's fixtures in
    `<fixtures>/devboard/` (ReplayProvider searches each area folder)."""
    return container.llm


def agent_runner(
    container: Container,
    sleep: Sleep,
    step_delay: float,
    redactor: TextRedactor | None = None,
) -> AgentRunner:
    """The simulation, or (AGENT_SANDBOX=docker, local only) the Docker writer sandbox with
    LocalGitHost. Under docker, plan runs go to the sandboxed dev-lead unless
    AGENT_PLANNER=simulation. Builds touch no disk."""
    simulated = SimulatedAgentRunner(sleep, step_delay=step_delay)
    settings = container.settings
    proxy_secret, coordinator_key = settings.llm_proxy_secret, settings.coordinator_token_key
    if settings.agent_sandbox != "docker" or proxy_secret is None or coordinator_key is None:
        return simulated
    config = SandboxConfig(
        image=settings.agent_sandbox_image,
        network=settings.agent_sandbox_network,
        egress_proxy=settings.agent_egress_proxy,
        llm_base_url=settings.agent_llm_base_url,
        coordinator_url=settings.agent_coordinator_url,
        run_timeout_s=settings.agent_run_timeout_s,
        plan_budget_usd=settings.agent_plan_budget_usd,
        plan_timeout_s=settings.agent_plan_timeout_s,
        triage_budget_usd=settings.agent_triage_budget_usd,
        triage_timeout_s=settings.agent_triage_timeout_s,
        triage_model=settings.agent_triage_model,
        artifacts_root=AGENT_ARTIFACTS,
    )
    deps = SandboxDeps(
        host=LocalGitHost(REPO_ROOT, settings.agent_remote_path),
        processes=AsyncioProcessRunner(),
        coordinator_tokens=CoordinatorTokenMint(coordinator_key.get_secret_value().encode()),
        planner=simulated if settings.agent_planner == "simulation" else None,
        usage=DockerUsageProbe(config),
        redactor=redactor,
    )
    return DockerAgentRunner(config, deps, proxy_secret.get_secret_value().encode())


class LabelledRunStore:
    """Labels runs with the agent that actually executes them (`labels`, e.g. the Docker
    runner's `writer` for dev runs) instead of the workflow's default, and keeps each run's
    final step on that label."""

    def __init__(self, inner: RunStore, labels: Mapping[RunKind, str]) -> None:
        self._inner = inner
        self._labels = dict(labels)

    async def insert(self, user_id: uuid.UUID, run: RunRecord) -> None:
        label = self._labels.get(run.kind)
        await self._inner.insert(user_id, replace(run, agent=label) if label else run)

    async def get(self, user_id: uuid.UUID, run_id: uuid.UUID) -> RunRecord | None:
        return await self._inner.get(user_id, run_id)

    async def for_task(
        self, user_id: uuid.UUID, task_id: uuid.UUID, offset: int, limit: int
    ) -> list[RunRecord]:
        return await self._inner.for_task(user_id, task_id, offset, limit)

    async def active(self, user_id: uuid.UUID, task_id: uuid.UUID) -> list[RunRecord]:
        return await self._inner.active(user_id, task_id)

    async def mark_running(self, user_id: uuid.UUID, run_id: uuid.UUID, at: datetime) -> None:
        await self._inner.mark_running(user_id, run_id, at)

    async def append_step(
        self, user_id: uuid.UUID, run_id: uuid.UUID, step: NewStep, at: datetime
    ) -> StepRecord:
        return await self._inner.append_step(user_id, run_id, step, at)

    async def finish(
        self, user_id: uuid.UUID, run_id: uuid.UUID, finish: RunFinish, final: NewStep
    ) -> RunRecord | None:
        stored = await self._inner.get(user_id, run_id)
        if stored is not None and stored.agent in self._labels.values():
            final = replace(final, agent=stored.agent)
        return await self._inner.finish(user_id, run_id, finish, final)

    async def steps_after(
        self, user_id: uuid.UUID, run_id: uuid.UUID, after_seq: int, limit: int
    ) -> list[StepRecord]:
        return await self._inner.steps_after(user_id, run_id, after_seq, limit)


def _labelled(stores: BoardStores, runner: AgentRunner) -> BoardStores:
    if not isinstance(runner, DockerAgentRunner):
        return stores
    return replace(stores, runs=LabelledRunStore(stores.runs, runner.labels))


def repo_files(container: Container) -> RepoFiles:
    """The repo's files for path checks. Under replay a frozen listing keeps offline prompt
    hashes stable; otherwise the working tree (`.gitignore`d files left out)."""
    settings = container.settings
    if settings.llm == "replay":
        return ListedRepoFiles(settings.llm_fixtures_dir / "devboard" / "repo_files.txt")
    return WorkingTreeFiles(REPO_ROOT)


def triage_agent(container: Container, runner: AgentRunner, files: RepoFiles) -> TriageAgent:
    """The read-only sandbox triage under AGENT_SANDBOX=docker (unless AGENT_TRIAGER=in_app),
    else the in-app structured call."""
    if isinstance(runner, DockerAgentRunner) and container.settings.agent_triager == "sandbox":
        return DockerTriageAgent(runner, container.guard, container.pii)
    return TriageLlmAgent(devboard_llm(container), container.guard, container.pii, files)


def build(container: Container, *, sleep: Sleep = asyncio.sleep) -> DevboardRuntime:
    settings, clock, ids = container.settings, container.clock, container.ids
    redactor = Redactor(settings.denylist_values(), container.pii, container.guard)
    delay = 0.0 if settings.environment == "test" else STEP_DELAY_SECONDS
    runner = agent_runner(container, sleep, delay, redactor)
    stores = _labelled(board_stores(container), runner)
    board = BoardService(stores.tasks, container.pii, clock, ids)
    runs = RunService(stores.runs, stores.tasks, stores.hub, redactor, clock)
    plans = PlanService(stores.tasks, board, clock, ids)
    leases = LeaseService(stores.leases, clock)
    dispatcher = InProcessDispatcher()
    files = repo_files(container)
    agents = Agents(triage=triage_agent(container, runner, files), runner=runner)
    workflow = Workflow(
        Stores(tasks=stores.tasks, runs=stores.runs),
        (board, runs, plans, leases),
        agents,
        dispatcher,
        (clock, ids),
        repo_files=files,
    )
    board.attach(workflow)
    chats = task_chat_store(container)
    chat = TaskChatService(
        (board, plans),
        chats,
        TaskChatLlmAgent(devboard_llm(container)),
        (container.guard, container.pii, GuardRedaction(redactor)),
        (clock, ids),
    )
    services = DevboardServices(
        board=board,
        runs=runs,
        plans=plans,
        workflow=workflow,
        redactor=redactor,
        chat=chat,
        proposals=ProposalDecisions((board, plans, workflow), chats, (clock, ids)),
    )
    return DevboardRuntime(services=services, stores=stores, leases=leases, dispatcher=dispatcher)


def install(app: FastAPI, container: Container) -> None:
    runtime = build(container)
    app.state.devboard = runtime.services
    app.state.devboard_runtime = runtime
    app.include_router(task_routes.router)
    app.include_router(run_routes.router)
    app.include_router(chat_routes.router)


# ------------------------------------------------------------------ scheduled jobs
_START_JOB = text("INSERT INTO job_runs (job, started_at) VALUES (:job, :at) RETURNING id")
_FINISH_JOB = text(
    "UPDATE job_runs SET finished_at = :at, ok = :ok, items_seen = :seen, items_new = :new,"
    " error_code = :code WHERE id = :id"
)


class PgJobRunLog:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def start(self, job: JobName, at: datetime) -> uuid.UUID:
        async with system_transaction(self._sessions) as session:
            run_id = await session.scalar(_START_JOB, {"job": job.value, "at": at})
        assert isinstance(run_id, uuid.UUID)
        return run_id

    async def finish(self, run_id: uuid.UUID, report: JobReport, at: datetime) -> None:
        values = {
            "id": run_id,
            "at": at,
            "ok": report.ok,
            "seen": report.items_seen,
            "new": report.items_new,
            "code": report.error_code,
        }
        async with system_transaction(self._sessions) as session:
            await session.execute(_FINISH_JOB, values)


class InMemoryJobRunLog:
    def __init__(self) -> None:
        self.runs: dict[uuid.UUID, tuple[JobName, datetime, datetime | None, JobReport | None]] = {}

    async def start(self, job: JobName, at: datetime) -> uuid.UUID:
        run_id = uuid.uuid4()
        self.runs[run_id] = (job, at, None, None)
        return run_id

    async def finish(self, run_id: uuid.UUID, report: JobReport, at: datetime) -> None:
        job, started, _, _ = self.runs[run_id]
        self.runs[run_id] = (job, started, at, report)


def job_run_log(container: Container) -> JobRunLog:
    if container.engine is None:
        return InMemoryJobRunLog()
    return PgJobRunLog(session_factory(container.engine))


def job_actor(container: Container) -> Actor:
    """Jobs act for the maintainer account (RLS scope and LLM attribution)."""
    entry = container.settings.allowlist().entry_for_role(AppRole.MAINTAINER)
    user_id = entry.user_id if entry is not None else uuid.UUID(int=0)
    return Actor(user_id=user_id, kind=ActorKind.SYSTEM, role=AppRole.MAINTAINER)
