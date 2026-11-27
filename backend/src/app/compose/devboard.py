"""Dev-board area: stores, agents, services and routes, built from the `Container`.

Postgres stores when the container has an engine, in-memory fakes otherwise. Step streams use
the container's shared LISTEN hub (ARCH-31), or an in-memory hub offline.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.agents.docker_runner import DockerAgentRunner, SandboxConfig, SandboxDeps
from app.adapters.agents.process import AsyncioProcessRunner
from app.adapters.agents.tokens import CoordinatorTokenMint
from app.adapters.codehost.local_git import LocalGitHost
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
from app.domain.devboard.model import Actor, ActorKind
from app.domain.devboard.plans import PlanService
from app.domain.devboard.runs import RunService
from app.domain.devboard.schedule import JobName, JobReport, JobRunLog
from app.domain.devboard.simulation import SimulatedAgentRunner
from app.domain.devboard.task_chat import TaskChatService
from app.domain.devboard.task_chat_decisions import ProposalDecisions
from app.domain.devboard.workflow import Agents, Stores, Workflow
from app.domain.identity import AppRole
from app.domain.ports import LLM, NotificationHub
from app.domain.ports.devboard import AgentRunner, LeaseStore, RunStore, TaskStore
from app.domain.ports.task_chat import TaskChatStore
from app.guards.redaction import Redactor
from app.settings import REPO_ROOT

if TYPE_CHECKING:
    from app.main import Container

type Sleep = Callable[[float], Awaitable[None]]

# Pause between simulated agent steps, so the live stream is visible in the UI.
STEP_DELAY_SECONDS = 0.6


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


def agent_runner(container: Container, sleep: Sleep, step_delay: float) -> AgentRunner:
    """The simulation, or (AGENT_SANDBOX=docker, local only) the Docker writer sandbox with
    LocalGitHost. Plan runs stay on the simulation either way. Builds touch no disk."""
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
    )
    deps = SandboxDeps(
        host=LocalGitHost(REPO_ROOT, settings.agent_remote_path),
        processes=AsyncioProcessRunner(),
        coordinator_tokens=CoordinatorTokenMint(coordinator_key.get_secret_value().encode()),
        planner=simulated,
    )
    return DockerAgentRunner(config, deps, proxy_secret.get_secret_value().encode())


def build(container: Container, *, sleep: Sleep = asyncio.sleep) -> DevboardRuntime:
    settings, clock, ids = container.settings, container.clock, container.ids
    stores = board_stores(container)
    redactor = Redactor(settings.denylist_values(), container.pii, container.guard)
    board = BoardService(stores.tasks, container.pii, clock, ids)
    runs = RunService(stores.runs, stores.tasks, stores.hub, redactor, clock)
    plans = PlanService(stores.tasks, board, clock, ids)
    leases = LeaseService(stores.leases, clock)
    dispatcher = InProcessDispatcher()
    delay = 0.0 if settings.environment == "test" else STEP_DELAY_SECONDS
    agents = Agents(
        triage=TriageLlmAgent(devboard_llm(container), container.guard, container.pii),
        runner=agent_runner(container, sleep, delay),
    )
    workflow = Workflow(
        Stores(tasks=stores.tasks, runs=stores.runs),
        (board, runs, plans, leases),
        agents,
        dispatcher,
        (clock, ids),
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
