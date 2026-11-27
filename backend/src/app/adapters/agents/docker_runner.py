"""DockerAgentRunner: the dev run in the local writer sandbox (`AGENT_SANDBOX=docker`).

Per run: snapshot the working tree (LocalGitHost), mint a task-scoped llm-proxy token and a
coordinator token, `docker run` the writer image with exactly `sandbox/run.sh`'s hardening, wait
under the wall-clock budget and the kill switch, then read `/out/handoff.json`, publish
`/out/result.bundle` as `agent/<task8>-<run8>` and report the branch and its diff stat.

- Steps reach the board through the coordinator, so this runner emits none.
- Tokens travel only in the child process env and reach the container as `-e NAME` (never in
  argv, never logged). The runner holds no model key: that lives only in llm-proxy's env.
- Plan runs (read-only dev-lead) go to `planner` (the simulation until a sandboxed dev-lead
  exists).
"""

from __future__ import annotations

import asyncio
import math
import os
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

from app.adapters.agents.handoff import Handoff, HandoffError, read_handoff
from app.adapters.agents.process import ProcessRunner
from app.adapters.agents.tokens import (
    CoordinatorTokens,
    CoordinatorTokensUnavailable,
    mint_llm_proxy_token,
)
from app.adapters.codehost.local_git import CodeHost, DiffStat, GitError, Snapshot, branch_name
from app.domain.devboard.model import PlanRecord, RunKind
from app.domain.ports.devboard import AgentResult, AgentRunner, RunContext, RunTools

# Mirrors sandbox/run.sh flag for flag; a unit test checks each one still appears there.
SANDBOX_UID = "10001"
# The container's own tmpfs mount point (not a host temp dir).
CONTAINER_TMP = str(PurePosixPath("/") / "tmp")
HARDENING_FLAGS: tuple[tuple[str, ...], ...] = (
    ("--read-only",),
    ("--tmpfs", f"{CONTAINER_TMP}:rw,nosuid,nodev,size=2g"),
    ("--tmpfs", "/workspace:rw,exec,nosuid,nodev,size=4g,uid=10001,gid=10001,mode=0700"),
    ("--cap-drop", "ALL"),
    ("--security-opt", "no-new-privileges"),
    ("--user", f"{SANDBOX_UID}:{SANDBOX_UID}"),
    ("--pids-limit", "1024"),
    ("--memory", "6g"),
    ("--cpus", "2"),
)
NO_PROXY = "localhost,127.0.0.1,llm-proxy"
# The llm-proxy budget is in tokens; plans budget in USD. A blended rate, deliberately generous:
# the proxy's own LLM_PROXY_MAX_BUDGET caps it again.
TOKENS_PER_USD = 100_000
# What the docker CLI itself needs from the parent env; nothing else is inherited.
DOCKER_CLI_ENV = (
    "PATH",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "DOCKER_CERT_PATH",
    "DOCKER_TLS_VERIFY",
    "XDG_RUNTIME_DIR",
)


@dataclass(frozen=True)
class SandboxConfig:
    image: str
    network: str = "jobradar_sandbox"
    egress_proxy: str = "http://egress-proxy:3128"
    llm_base_url: str = "http://llm-proxy:8080"
    coordinator_url: str = "http://coordinator:8100"
    run_timeout_s: int = 3600
    docker: str = "docker"
    poll_s: float = 1.0
    work_root: Path | None = None


@dataclass(frozen=True)
class SandboxDeps:
    host: CodeHost
    processes: ProcessRunner
    coordinator_tokens: CoordinatorTokens
    planner: AgentRunner


@dataclass(frozen=True)
class SandboxResult(AgentResult):
    """`AgentResult` plus what the sandbox produced. The port has no fields for these yet."""

    branch: str | None = None
    diff_stat: DiffStat | None = None
    handoff: Handoff | None = None


type Stop = Literal["timeout", "cancelled"] | None


@dataclass(frozen=True)
class _Launch:
    argv: list[str]
    env: dict[str, str]
    name: str
    timeout_s: int


@dataclass
class _Workdir:
    root: Path
    inbox: Path = field(init=False)
    outbox: Path = field(init=False)

    def __post_init__(self) -> None:
        self.inbox = self.root / "in"
        self.outbox = self.root / "out"

    def create(self) -> None:
        self.inbox.mkdir()
        self.outbox.mkdir()
        # Docker Desktop maps ownership; on Linux the container's uid 10001 needs these modes.
        # The parent stays 0700, so nobody else on the host can reach them.
        self.inbox.chmod(0o755)
        self.outbox.chmod(0o777)


def container_name(task_id: uuid.UUID, run_id: uuid.UUID) -> str:
    return f"jr-sandbox-{task_id.hex[:8]}-{run_id.hex[:8]}"


def _mount(source: Path, target: str, *, readonly: bool = False) -> str:
    text = str(source)
    if "," in text or "=" in text:
        raise ValueError("Sandbox mount paths must not contain ',' or '='.")
    suffix = ",readonly" if readonly else ""
    return f"type=bind,source={text},target={target}{suffix}"


class DockerAgentRunner:
    def __init__(
        self,
        config: SandboxConfig,
        deps: SandboxDeps,
        llm_proxy_secret: bytes,
        *,
        wall: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._deps = deps
        self.__secret = llm_proxy_secret
        self._wall = wall
        self._monotonic = monotonic

    def __repr__(self) -> str:
        return f"DockerAgentRunner(image={self._config.image!r})"

    async def run(self, context: RunContext, tools: RunTools) -> AgentResult:
        if context.run.kind is RunKind.PLAN:
            return await self._deps.planner.run(context, tools)
        plan = context.plan
        if plan is None or not plan.is_approved:
            return SandboxResult(is_success=False, outcome="No approved plan.")
        root = Path(
            await asyncio.to_thread(tempfile.mkdtemp, prefix="jr-run-", dir=self._config.work_root)
        )
        try:
            work = _Workdir(root)
            await asyncio.to_thread(work.create)
            return await self._run(context, tools, plan, work)
        finally:
            await asyncio.to_thread(shutil.rmtree, root, ignore_errors=True)

    def _timeout_s(self, plan: PlanRecord) -> int:
        return max(0, min(self._config.run_timeout_s, plan.budget_minutes * 60))

    async def _run(
        self, context: RunContext, tools: RunTools, plan: PlanRecord, work: _Workdir
    ) -> AgentResult:
        try:
            snapshot = await self._deps.host.snapshot(work.inbox / "input.bundle")
        except GitError:
            return SandboxResult(is_success=False, outcome="Could not snapshot the repository.")
        try:
            launch = self._launch(context, plan, work)
        except (CoordinatorTokensUnavailable, ValueError) as error:
            # Both carry fixed, secret-free messages (bad key length, ttl, mount path).
            return SandboxResult(is_success=False, outcome=str(error))
        code, stopped = await self._supervise(launch, tools)
        if stopped == "cancelled":
            return SandboxResult(is_success=False, outcome="Stopped.")
        if stopped == "timeout":
            return SandboxResult(
                is_success=False, outcome=f"Timed out after {launch.timeout_s} s; sandbox killed."
            )
        if code != 0:
            return SandboxResult(is_success=False, outcome=f"The sandbox failed (exit {code}).")
        return await self._collect(context, work, snapshot)

    def _launch(self, context: RunContext, plan: PlanRecord, work: _Workdir) -> _Launch:
        task_id, run_id = context.task.id, context.run.id
        timeout_s = self._timeout_s(plan)
        budget = max(1, math.ceil(plan.budget_usd * TOKENS_PER_USD))
        llm_token = mint_llm_proxy_token(
            self.__secret, str(task_id), timeout_s, budget, now=self._wall()
        )
        coordinator_token = self._deps.coordinator_tokens.mint(task_id, run_id, timeout_s)
        name = container_name(task_id, run_id)
        cfg = self._config
        public = {
            "TASK_ID": str(task_id),
            "RUN_ID": str(run_id),
            "HTTPS_PROXY": cfg.egress_proxy,
            "HTTP_PROXY": cfg.egress_proxy,
            "https_proxy": cfg.egress_proxy,
            "http_proxy": cfg.egress_proxy,
            "NO_PROXY": NO_PROXY,
            "no_proxy": NO_PROXY,
            "ANTHROPIC_BASE_URL": cfg.llm_base_url,
            "COORDINATOR_URL": cfg.coordinator_url,
            "AGENT_TIMEOUT_S": str(timeout_s),
            "AGENT_BUDGET_USD": str(plan.budget_usd),
            "AGENT_BUDGET_TOKENS": str(budget),
        }
        secrets = {"ANTHROPIC_AUTH_TOKEN": llm_token, "COORDINATOR_TOKEN": coordinator_token}
        argv = [cfg.docker, "run", "--rm", "--name", name, "--network", cfg.network]
        for flag in HARDENING_FLAGS:
            argv.extend(flag)
        argv += ["--mount", _mount(work.inbox, "/in", readonly=True)]
        argv += ["--mount", _mount(work.outbox, "/out")]
        for key, value in public.items():
            argv += ["-e", f"{key}={value}"]
        for key in secrets:
            argv += ["-e", key]
        argv.append(cfg.image)
        env = _child_env(os.environ, secrets)
        return _Launch(argv=argv, env=env, name=name, timeout_s=timeout_s)

    async def _supervise(self, launch: _Launch, tools: RunTools) -> tuple[int, Stop]:
        stop = threading.Event()
        kill_argv = [self._config.docker, "kill", launch.name]
        job = asyncio.ensure_future(
            self._deps.processes.run(launch.argv, launch.env, stop=stop, kill_argv=kill_argv)
        )
        deadline = self._monotonic() + launch.timeout_s
        stopped: Stop = None
        try:
            while not job.done():
                await asyncio.wait({job}, timeout=self._config.poll_s)
                if job.done() or stopped is not None:
                    continue
                if self._monotonic() >= deadline:
                    stopped = "timeout"
                elif await tools.is_cancelled():
                    stopped = "cancelled"
                if stopped is not None:
                    stop.set()
        except asyncio.CancelledError:
            stop.set()
            await asyncio.wait({job})
            raise
        return job.result(), stopped

    async def _collect(
        self, context: RunContext, work: _Workdir, snapshot: Snapshot
    ) -> AgentResult:
        task_id, run_id = context.task.id, context.run.id
        try:
            handoff = await asyncio.to_thread(
                read_handoff, work.outbox / "handoff.json", str(task_id)
            )
        except HandoffError as error:
            return SandboxResult(is_success=False, outcome=str(error))
        bundle = work.outbox / "result.bundle"
        if not await asyncio.to_thread(bundle.is_file):
            return SandboxResult(
                is_success=False, outcome="The sandbox wrote no result bundle.", handoff=handoff
            )
        branch = branch_name(task_id, run_id)
        try:
            published = await self._deps.host.publish(
                bundle, f"agent/{task_id}", branch, snapshot.base_sha
            )
        except (GitError, ValueError):
            return SandboxResult(
                is_success=False, outcome="Could not publish the agent branch.", handoff=handoff
            )
        stat, tests = published.stat, handoff.tests
        # Built from counts only: the handoff's free text is untrusted and stays in `handoff`.
        outcome = (
            f"Branch {branch}: {stat.files_changed} files changed, +{stat.insertions}"
            f" -{stat.deletions}; handoff {handoff.status}; tests {tests.passed} passed,"
            f" {tests.failed} failed."
        )
        return SandboxResult(
            is_success=handoff.status == "done",
            outcome=outcome,
            branch=branch,
            diff_stat=stat,
            handoff=handoff,
        )


def _child_env(parent: Mapping[str, str], secrets: Mapping[str, str]) -> dict[str, str]:
    env = {name: parent[name] for name in DOCKER_CLI_ENV if name in parent}
    env.update(secrets)
    return env
