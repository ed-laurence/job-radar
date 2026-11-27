"""DockerAgentRunner: the dev run in the local writer sandbox (`AGENT_SANDBOX=docker`).

Per run: snapshot the working tree (LocalGitHost), mint a task-scoped `jrt2` llm-proxy token
(budget = the plan's USD budget) and a coordinator token, `docker run` the writer image with
exactly `sandbox/run.sh`'s hardening and env, wait under the wall-clock budget and the kill
switch, then read `/out/handoff.json`, publish `/out/result.bundle` as `agent/<task8>-<run8>`,
ask llm-proxy what the run spent, and keep the run's logs in `.artifacts/agent-runs/<run_id>/`.

- Steps reach the board through the coordinator, so this runner emits none.
- Tokens travel only in the child process env and reach the container as `-e NAME` (never in
  argv, never logged, scrubbed from kept artifacts). The runner holds no model key: that lives
  only in llm-proxy's env.
- A failed handoff's summary (untrusted: one line, capped, redacted) is the run's outcome, which
  the card shows as the blocked reason. A successful outcome is built from counts only.
- Plan runs (read-only dev-lead) go to `planner` (the simulation until a sandboxed dev-lead
  exists). Dev runs are labelled with the sandbox's role, `writer` (`labels`).
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from app.adapters.agents.handoff import Handoff, HandoffError, read_handoff
from app.adapters.agents.process import ProcessRunner
from app.adapters.agents.sandbox_spec import (
    DOCKER_CLI_ENV,
    HARDENING_FLAGS,
    NO_PROXY,
    SandboxConfig,
    child_env,
)
from app.adapters.agents.tokens import (
    CoordinatorTokens,
    CoordinatorTokensUnavailable,
    mint_llm_proxy_budget_token,
    usd_to_micro,
)
from app.adapters.agents.usage import ProxyUsage, UsageProbe
from app.adapters.codehost.local_git import CodeHost, DiffStat, GitError, Snapshot, branch_name
from app.domain.devboard.model import PlanRecord, RunKind
from app.domain.errors import DomainError
from app.domain.ports.devboard import (
    AgentResult,
    AgentRunner,
    RunContext,
    RunTools,
    TextRedactor,
)

__all__ = [
    "AGENT_ROLE",
    "ARTIFACTS_HINT",
    "DOCKER_CLI_ENV",
    "HARDENING_FLAGS",
    "NO_PROXY",
    "DockerAgentRunner",
    "SandboxConfig",
    "SandboxDeps",
    "SandboxResult",
    "container_name",
]

# The role the sandbox plays; the card shows it as the dev run's agent.
AGENT_ROLE = "writer"
# Where a run's logs are kept, relative to the repo (shown on the card when there's no handoff).
ARTIFACTS_HINT = "see .artifacts/agent-runs/{run_id}/"
# The llm-proxy token outlives the wall clock a little, so the spend can still be read after a
# timeout kill. The container is gone by then; the token is task-scoped and budgeted.
USAGE_GRACE_S = 300
KEPT_FILES = ("handoff.json", "agent.log", "steps.jsonl")
RUNNER_LOG = "runner.log"
MAX_KEPT_BYTES = 256 * 1024
MAX_RUNNER_LOG_BYTES = 64 * 1024
MAX_REASON_CHARS = 300
REDACTED = "[redacted]"
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class SandboxDeps:
    host: CodeHost
    processes: ProcessRunner
    coordinator_tokens: CoordinatorTokens
    planner: AgentRunner
    # llm-proxy `GET /v1/usage`; None means the spend is always unknown.
    usage: UsageProbe | None = None
    # Redacts the handoff reason and kept artifacts; None leaves only the token scrub.
    redactor: TextRedactor | None = None


@dataclass(frozen=True)
class SandboxResult(AgentResult):
    """`AgentResult` plus what the sandbox produced. The port has no fields for these yet."""

    branch: str | None = None
    diff_stat: DiffStat | None = None
    handoff: Handoff | None = None
    # llm-proxy's answer; None when it was unreachable (the spend is unknown, not zero).
    spend: ProxyUsage | None = None


type Stop = Literal["timeout", "cancelled"] | None


@dataclass(frozen=True)
class _Launch:
    argv: list[str]
    env: dict[str, str]
    name: str
    timeout_s: int
    llm_token: str
    secrets: tuple[str, ...]


@dataclass
class _Workdir:
    root: Path
    inbox: Path = field(init=False)
    outbox: Path = field(init=False)
    console: Path = field(init=False)

    def __post_init__(self) -> None:
        self.inbox = self.root / "in"
        self.outbox = self.root / "out"
        # Outside both mounts: the container can't touch the runner's own log.
        self.console = self.root / RUNNER_LOG

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

    @property
    def labels(self) -> Mapping[RunKind, str]:
        """The agent label of each run kind this runner executes itself."""
        return {RunKind.DEV: AGENT_ROLE}

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
        code, stopped = await self._supervise(launch, tools, work)
        spend = await self._spend(launch.llm_token)
        try:
            result = await self._result(context, work, snapshot, (code, stopped), launch)
        finally:
            await self._keep_artifacts(context.run.id, work, launch.secrets)
        return _with_spend(result, spend)

    def _launch(self, context: RunContext, plan: PlanRecord, work: _Workdir) -> _Launch:
        task_id, run_id = context.task.id, context.run.id
        timeout_s = self._timeout_s(plan)
        llm_token = mint_llm_proxy_budget_token(
            self.__secret,
            str(task_id),
            timeout_s + USAGE_GRACE_S,
            usd_to_micro(plan.budget_usd),
            now=self._wall(),
        )
        coordinator_token = self._deps.coordinator_tokens.mint(task_id, run_id, timeout_s)
        name = container_name(task_id, run_id)
        cfg = self._config
        # Exactly sandbox/run.sh's env: its fixed values, then pass-through names it knows.
        public = {
            "TASK_ID": str(task_id),
            "HTTPS_PROXY": cfg.egress_proxy,
            "HTTP_PROXY": cfg.egress_proxy,
            "https_proxy": cfg.egress_proxy,
            "http_proxy": cfg.egress_proxy,
            "NO_PROXY": NO_PROXY,
            "no_proxy": NO_PROXY,
            "ANTHROPIC_BASE_URL": cfg.llm_base_url,
            "COORDINATOR_URL": cfg.coordinator_url,
            "RUN_ID": str(run_id),
            "AGENT_TIMEOUT_S": str(timeout_s),
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
        return _Launch(
            argv=argv,
            env=child_env(os.environ, secrets),
            name=name,
            timeout_s=timeout_s,
            llm_token=llm_token,
            secrets=(*secrets.values(), self.__secret.decode("utf-8", "ignore")),
        )

    async def _supervise(
        self, launch: _Launch, tools: RunTools, work: _Workdir
    ) -> tuple[int, Stop]:
        stop = threading.Event()
        kill_argv = [self._config.docker, "kill", launch.name]
        job = asyncio.ensure_future(
            self._deps.processes.run(
                launch.argv, launch.env, stop=stop, kill_argv=kill_argv, output=work.console
            )
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

    async def _spend(self, llm_token: str) -> ProxyUsage | None:
        if self._deps.usage is None:
            return None
        return await self._deps.usage.usage(llm_token)

    async def _result(
        self,
        context: RunContext,
        work: _Workdir,
        snapshot: Snapshot,
        exit_status: tuple[int, Stop],
        launch: _Launch,
    ) -> SandboxResult:
        code, stopped = exit_status
        if stopped is not None:
            return _stopped(stopped, launch.timeout_s)
        try:
            handoff = await asyncio.to_thread(
                read_handoff, work.outbox / "handoff.json", str(context.task.id)
            )
        except HandoffError:
            hint = ARTIFACTS_HINT.format(run_id=context.run.id)
            lead = f"The sandbox failed (exit {code}); the agent" if code != 0 else "The agent"
            return SandboxResult(is_success=False, outcome=f"{lead} produced no handoff ({hint}).")
        if handoff.status != "done":
            reason = await self._reason(handoff, launch.secrets)
            return await self._failed(context, work, snapshot, handoff, (code, reason))
        if code != 0:
            outcome = f"The sandbox failed (exit {code})."
            return SandboxResult(is_success=False, outcome=outcome, handoff=handoff)
        published = await self._publish(context, work, snapshot, handoff)
        if isinstance(published, str):
            return SandboxResult(is_success=False, outcome=published, handoff=handoff)
        return published

    async def _failed(
        self,
        context: RunContext,
        work: _Workdir,
        snapshot: Snapshot,
        handoff: Handoff,
        exit_reason: tuple[int, str],
    ) -> SandboxResult:
        """A failed handoff: its reason leads. After a clean exit the branch is still published
        (so the work can be inspected) and its counts follow the reason."""
        code, reason = exit_reason
        failed = SandboxResult(is_success=False, outcome=reason, handoff=handoff)
        if code != 0:
            return failed
        published = await self._publish(context, work, snapshot, handoff)
        if isinstance(published, str):
            return failed
        return replace(published, is_success=False, outcome=f"{reason} {published.outcome}")

    async def _publish(
        self, context: RunContext, work: _Workdir, snapshot: Snapshot, handoff: Handoff
    ) -> SandboxResult | str:
        """The published branch as a successful result, or why it couldn't be published."""
        task_id, run_id = context.task.id, context.run.id
        bundle = work.outbox / "result.bundle"
        if not await asyncio.to_thread(_is_regular_file, bundle):
            return "The sandbox wrote no result bundle."
        branch = branch_name(task_id, run_id)
        try:
            published = await self._deps.host.publish(
                bundle, f"agent/{task_id}", branch, snapshot.base_sha
            )
        except (GitError, ValueError):
            return "Could not publish the agent branch."
        stat, tests = published.stat, handoff.tests
        # Built from counts only: the handoff's free text is untrusted and stays in `handoff`.
        outcome = (
            f"Branch {branch}: {stat.files_changed} files changed, +{stat.insertions}"
            f" -{stat.deletions}; handoff {handoff.status}; tests {tests.passed} passed,"
            f" {tests.failed} failed."
        )
        return SandboxResult(
            is_success=True, outcome=outcome, branch=branch, diff_stat=stat, handoff=handoff
        )

    async def _reason(self, handoff: Handoff, secrets: tuple[str, ...]) -> str:
        """The failed handoff's summary as a card reason: one line, capped, scrubbed, redacted."""
        text = _one_line(_scrub(handoff.summary, secrets))
        if len(text) > MAX_REASON_CHARS:
            text = text[: MAX_REASON_CHARS - 1].rstrip() + "…"
        if not text:
            return f"The agent reported {handoff.status} without a reason."
        return await self._redact(text) or "The agent's reason was withheld by redaction."

    async def _redact(self, text: str) -> str | None:
        if self._deps.redactor is None:
            return text
        try:
            return await self._deps.redactor.redact(text)
        except DomainError:
            return None

    async def _keep_artifacts(
        self, run_id: uuid.UUID, work: _Workdir, secrets: tuple[str, ...]
    ) -> None:
        root = self._config.artifacts_root
        if root is None:
            return
        texts: dict[str, str] = {}
        sources = [(name, work.outbox / name, MAX_KEPT_BYTES) for name in KEPT_FILES]
        sources.append((RUNNER_LOG, work.console, MAX_RUNNER_LOG_BYTES))
        for name, source, limit in sources:
            raw = await asyncio.to_thread(_tail, source, limit)
            if raw is None:
                continue
            clean = await self._redact(_scrub(raw, secrets))
            texts[name] = clean if clean is not None else "[withheld: redaction failed]\n"
        dest = root / str(run_id)
        await asyncio.to_thread(_write_artifacts, dest, texts)
        await asyncio.to_thread(_prune, root, self._config.keep_runs, dest)


def _stopped(stopped: Literal["timeout", "cancelled"], timeout_s: int) -> SandboxResult:
    if stopped == "cancelled":
        return SandboxResult(is_success=False, outcome="Stopped.")
    return SandboxResult(
        is_success=False, outcome=f"Timed out after {timeout_s} s; sandbox killed."
    )


def _with_spend(result: SandboxResult, spend: ProxyUsage | None) -> SandboxResult:
    if spend is not None:
        return replace(result, cost_usd=spend.used_usd, spend=spend)
    return replace(
        result, outcome=f"{result.outcome} Model spend unknown (llm-proxy usage unavailable)."
    )


def _scrub(text: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


def _one_line(text: str) -> str:
    text = _CONTROL.sub(" ", _ANSI.sub("", text))
    return _SPACES.sub(" ", text).strip()


def _is_regular_file(path: Path) -> bool:
    return not path.is_symlink() and path.is_file()


def _tail(path: Path, limit: int) -> str | None:
    """Blocking. The last `limit` bytes of a regular file (never a symlink the agent planted)."""
    if not _is_regular_file(path):
        return None
    with path.open("rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        handle.seek(max(0, size - limit))
        data = handle.read(limit)
    return data.decode("utf-8", "replace")


def _write_artifacts(dest: Path, texts: Mapping[str, str]) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name, text in texts.items():
        (dest / name).write_text(text, encoding="utf-8")


def _prune(root: Path, keep: int, current: Path) -> None:
    """Keep the newest `keep` run folders (the current one always); other entries stay."""
    runs: list[tuple[float, Path]] = []
    for entry in root.iterdir():
        if entry == current or entry.is_symlink() or not entry.is_dir():
            continue
        try:
            uuid.UUID(entry.name)
        except ValueError:
            continue
        runs.append((entry.stat().st_mtime, entry))
    runs.sort(key=lambda item: item[0], reverse=True)
    for _, stale in runs[max(0, keep - 1) :]:
        shutil.rmtree(stale, ignore_errors=True)
