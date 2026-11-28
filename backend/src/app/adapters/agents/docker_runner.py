"""DockerAgentRunner: dev, plan and triage runs in the local writer sandbox (AGENT_SANDBOX=docker).

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
- Plan runs (the dev-lead, `AGENT_MODE=plan`) use the same image, hardening and env. There is
  no plan yet, so the budget and wall clock come from config (`plan_budget_usd`,
  `plan_timeout_s`). Nothing is published: the result is `/out/plan.json`, read strictly
  (`plan_file`) into the draft, which the domain then checks (`finalise_draft`). With a
  `planner` in the deps (AGENT_PLANNER=simulation), plan runs stay on it instead.
- Triage runs (the read-only triager, `AGENT_MODE=triage`) work like plan runs: budget, wall
  clock and model from config (`triage_*`), nothing published, `/out/triage.json` read strictly
  (`triage_file`) into `SandboxResult.triage`. `DockerTriageAgent` (`docker_triage`) guards the
  card first and turns the result into the `TriageAgent` port's decision.
- Review runs (the read-only reviewer, `AGENT_MODE=review`) work like triage runs (budget,
  wall clock and model from config, `review_*`), but their input is the card's published branch
  head (`CodeHost.bundle_branch`) and the container gets the card's base as `AGENT_BASE_SHA`.
  `/out/review.json` is read strictly (`review_file`) into `AgentResult.review`.
- Dev runs: after publishing, the Publisher's checks run (`publish_checks`); any violation
  deletes the branch and fails the run with the list. A clean branch sets `branch` and
  `base_sha`. A rework dev run (the context carries a review and the card has a branch) starts
  from the card's branch head instead of a working-tree snapshot, with the card's base.
- `CODE_HOST=github` (a `pusher` in the deps, docs/features/github-prs.md): the host snapshots
  GitHub `main`, and a done dev run whose branch passed the checks gets the publish commit (the
  sandbox's test re-run counts and PUB-SIZE's changed lines as trailers) and is pushed to
  `agent/task-<uuid>`. `branch` stays the scratch branch. A failed push fails the run with the
  git verb and exit code; the scratch branch stays, so the card can be retried.
- Dev runs are labelled with the sandbox's role, `writer`; sandboxed plan runs `dev-lead`,
  triage runs `triage`, review runs `reviewer`.
"""

from __future__ import annotations

import asyncio
import contextlib
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
from typing import Literal, Protocol

from app.adapters.agents.handoff import Handoff, HandoffError, read_handoff
from app.adapters.agents.plan_file import PlanFileError, read_plan
from app.adapters.agents.process import ProcessRunner
from app.adapters.agents.publish_checks import (
    SecretFilter,
    branch_violations,
    changed_lines,
    refusal,
)
from app.adapters.agents.review_file import ReviewFileError, read_review
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
from app.adapters.agents.triage_file import TriageFileError, read_triage
from app.adapters.agents.usage import ProxyUsage, UsageProbe
from app.adapters.codehost.github import PublishTrailers, PushedBranch
from app.adapters.codehost.local_git import CodeHost, DiffStat, GitError, Snapshot, branch_name
from app.domain.devboard.model import ReviewSeverity, RunKind
from app.domain.devboard.publisher import DevRunSummary, dev_run_outcome
from app.domain.errors import DomainError
from app.domain.ports.devboard import (
    AgentResult,
    AgentRunner,
    ChangeSet,
    RunContext,
    RunTools,
    TextRedactor,
    TriageDecision,
    github_branch,
)

__all__ = [
    "AGENT_ROLE",
    "ARTIFACTS_HINT",
    "DOCKER_CLI_ENV",
    "HARDENING_FLAGS",
    "NO_PROXY",
    "BranchPusher",
    "DockerAgentRunner",
    "SandboxConfig",
    "SandboxDeps",
    "SandboxResult",
    "container_name",
]

# The role the sandbox plays; the card shows it as the dev run's agent.
AGENT_ROLE = "writer"
# The sandboxed plan run's label (the read-only dev-lead) and its sandbox mode.
PLANNER_ROLE = "dev-lead"
PLAN_MODE = "plan"
TRIAGE_ROLE = "triage"
TRIAGE_MODE = "triage"
REVIEW_ROLE = "reviewer"
REVIEW_MODE = "review"
# Where a run's logs are kept, relative to the repo (shown on the card when there's no handoff).
ARTIFACTS_HINT = "see .artifacts/agent-runs/{run_id}/"
# The llm-proxy token outlives the wall clock a little, so the spend can still be read after a
# timeout kill. The container is gone by then; the token is task-scoped and budgeted.
USAGE_GRACE_S = 300
KEPT_FILES = (
    "handoff.json",
    "plan.json",
    "triage.json",
    "review.json",
    "agent.log",
    "steps.jsonl",
)
RUNNER_LOG = "runner.log"
MAX_KEPT_BYTES = 256 * 1024
MAX_RUNNER_LOG_BYTES = 64 * 1024
MAX_REASON_CHARS = 300
REDACTED = "[redacted]"
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_SPACES = re.compile(r"\s+")


class BranchPusher(Protocol):
    """`GitHubHost.push`: the publish commit on a checked scratch branch, pushed to GitHub."""

    async def push(self, branch: str, trailers: PublishTrailers) -> PushedBranch: ...


@dataclass(frozen=True)
class SandboxDeps:
    host: CodeHost
    processes: ProcessRunner
    coordinator_tokens: CoordinatorTokens
    # Runs plan runs instead of the sandbox (the simulation); None plans in the sandbox.
    planner: AgentRunner | None = None
    # llm-proxy `GET /v1/usage`; None means the spend is always unknown.
    usage: UsageProbe | None = None
    # Redacts the handoff reason and kept artifacts; None leaves only the token scrub.
    redactor: TextRedactor | None = None
    # PUB-SECRET's rules (the redaction filter's fast path); None falls back to `redactor`.
    secret_filter: SecretFilter | None = None
    # CODE_HOST=github: pushes checked dev-run branches; None keeps them in the scratch remote.
    pusher: BranchPusher | None = None


@dataclass(frozen=True)
class SandboxResult(AgentResult):
    """`AgentResult` plus what the sandbox produced. The port has no fields for these yet."""

    diff_stat: DiffStat | None = None
    handoff: Handoff | None = None
    # llm-proxy's answer; None when it was unreachable (the spend is unknown, not zero).
    spend: ProxyUsage | None = None
    # A triage run's decision (`/out/triage.json`); None for other runs and on failure.
    triage: TriageDecision | None = None


type Stop = Literal["timeout", "cancelled"] | None


@dataclass(frozen=True)
class _Budget:
    usd: float
    timeout_s: int
    # `AGENT_MODE` for the sandbox; None keeps its default (the dev agent).
    mode: str | None = None
    # `AGENT_MODEL` for the sandbox; None keeps the sandbox's default for the mode.
    model: str | None = None


@dataclass(frozen=True)
class _Launch:
    argv: list[str]
    env: dict[str, str]
    name: str
    timeout_s: int
    llm_token: str
    secrets: tuple[str, ...]


@dataclass(frozen=True)
class _Input:
    """The run's input bundle; `from_branch` when it is a published branch's head."""

    snapshot: Snapshot
    from_branch: bool = False


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
        labels = {
            RunKind.DEV: AGENT_ROLE,
            RunKind.TRIAGE: TRIAGE_ROLE,
            RunKind.REVIEW: REVIEW_ROLE,
        }
        if self._deps.planner is None:
            labels[RunKind.PLAN] = PLANNER_ROLE
        return labels

    async def run(self, context: RunContext, tools: RunTools) -> AgentResult:
        if context.run.kind is RunKind.TRIAGE:
            budget = _Budget(
                usd=self._config.triage_budget_usd,
                timeout_s=self._cap(self._config.triage_timeout_s),
                mode=TRIAGE_MODE,
                model=self._config.triage_model,
            )
            return await self._in_workdir(context, tools, budget)
        if context.run.kind is RunKind.REVIEW:
            budget = _Budget(
                usd=self._config.review_budget_usd,
                timeout_s=self._cap(self._config.review_timeout_s),
                mode=REVIEW_MODE,
                model=self._config.review_model,
            )
            return await self._in_workdir(context, tools, budget)
        if context.run.kind is RunKind.PLAN:
            if self._deps.planner is not None:
                return await self._deps.planner.run(context, tools)
            budget = _Budget(
                usd=self._config.plan_budget_usd,
                timeout_s=self._cap(self._config.plan_timeout_s),
                mode=PLAN_MODE,
            )
            return await self._in_workdir(context, tools, budget)
        plan = context.plan
        if plan is None or not plan.is_approved:
            return SandboxResult(is_success=False, outcome="No approved plan.")
        budget = _Budget(usd=plan.budget_usd, timeout_s=self._cap(plan.budget_minutes * 60))
        return await self._in_workdir(context, tools, budget)

    def _cap(self, timeout_s: int) -> int:
        return max(0, min(self._config.run_timeout_s, timeout_s))

    async def _in_workdir(
        self, context: RunContext, tools: RunTools, budget: _Budget
    ) -> AgentResult:
        root = Path(
            await asyncio.to_thread(tempfile.mkdtemp, prefix="jr-run-", dir=self._config.work_root)
        )
        try:
            work = _Workdir(root)
            await asyncio.to_thread(work.create)
            return await self._run(context, tools, budget, work)
        finally:
            await asyncio.to_thread(shutil.rmtree, root, ignore_errors=True)

    async def _run(
        self, context: RunContext, tools: RunTools, budget: _Budget, work: _Workdir
    ) -> AgentResult:
        given = await self._input(context, work, budget)
        if isinstance(given, str):
            return SandboxResult(is_success=False, outcome=given)
        snapshot = given.snapshot
        try:
            base = snapshot.base_sha if given.from_branch else None
            launch = self._launch(context, budget, work, base)
        except (CoordinatorTokensUnavailable, ValueError) as error:
            # Both carry fixed, secret-free messages (bad key length, ttl, mount path).
            return SandboxResult(is_success=False, outcome=str(error))
        code, stopped = await self._supervise(launch, tools, work)
        spend = await self._spend(launch.llm_token)
        try:
            if budget.mode == PLAN_MODE:
                result = await self._plan_result(context, work, (code, stopped), launch)
            elif budget.mode == TRIAGE_MODE:
                result = await self._triage_result(context, work, (code, stopped), launch)
                result = _with_model(result, budget.model)
            elif budget.mode == REVIEW_MODE:
                result = await self._review_result(context, work, (code, stopped), launch)
                result = _with_model(result, budget.model)
            else:
                result = await self._result(context, work, snapshot, (code, stopped), launch)
        finally:
            await self._keep_artifacts(context.run.id, work, launch.secrets)
        return _with_spend(result, spend)

    async def _input(self, context: RunContext, work: _Workdir, budget: _Budget) -> _Input | str:
        """A working-tree snapshot, or (review and rework runs) the card's branch head with the
        card's base. A string is why there is no input."""
        dest = work.inbox / "input.bundle"
        task = context.task
        has_branch = task.branch is not None and task.base_sha is not None
        is_rework = budget.mode is None and context.review is not None and has_branch
        if budget.mode == REVIEW_MODE or is_rework:
            if task.branch is None or task.base_sha is None:
                return "The card has no published branch to review."
            try:
                bundled = await self._deps.host.bundle_branch(task.branch, task.base_sha, dest)
            except (GitError, ValueError):
                return "Could not bundle the agent branch."
            return _Input(bundled, from_branch=True)
        try:
            return _Input(await self._deps.host.snapshot(dest))
        except GitError:
            return "Could not snapshot the repository."

    def _launch(
        self, context: RunContext, budget: _Budget, work: _Workdir, base_sha: str | None
    ) -> _Launch:
        task_id, run_id = context.task.id, context.run.id
        timeout_s = budget.timeout_s
        # The token's key is llm-proxy's spend ledger key: one per run, so earlier runs of the
        # same card don't use up this run's budget (fits the proxy's 64-character key).
        llm_token = mint_llm_proxy_budget_token(
            self.__secret,
            f"{task_id.hex[:8]}.{run_id}",
            timeout_s + USAGE_GRACE_S,
            usd_to_micro(budget.usd),
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
        if budget.mode is not None:
            public["AGENT_MODE"] = budget.mode
        if budget.model is not None:
            public["AGENT_MODEL"] = budget.model
        if base_sha is not None:
            # The workspace starts at a published branch head; the card's base is this commit.
            public["AGENT_BASE_SHA"] = base_sha
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
        published = await self._publish(context, work, snapshot, handoff, push=True)
        if isinstance(published, str):
            return SandboxResult(is_success=False, outcome=published, handoff=handoff)
        return published

    async def _plan_result(
        self, context: RunContext, work: _Workdir, exit_status: tuple[int, Stop], launch: _Launch
    ) -> SandboxResult:
        """The dev-lead's draft; nothing is published. A missing or invalid plan file fails the
        run with the parser's fixed reason and the artifacts hint."""
        code, stopped = exit_status
        if stopped is not None:
            return _stopped(stopped, launch.timeout_s)
        hint = ARTIFACTS_HINT.format(run_id=context.run.id)
        try:
            draft = await asyncio.to_thread(
                read_plan, work.outbox / "plan.json", str(context.task.id)
            )
        except PlanFileError as error:
            return await self._no_output(context, work, launch, (code, str(error)))
        if code != 0:
            outcome = f"The sandbox failed (exit {code}); its plan was not used ({hint})."
            return SandboxResult(is_success=False, outcome=outcome)
        # Built from counts only: the plan's free text is untrusted.
        outcome = (
            f"Plan ready for review: {len(draft.subtasks)} subtasks,"
            f" {len(draft.acceptance_tests)} acceptance tests."
        )
        return SandboxResult(is_success=True, outcome=outcome, plan=draft)

    async def _triage_result(
        self, context: RunContext, work: _Workdir, exit_status: tuple[int, Stop], launch: _Launch
    ) -> SandboxResult:
        """The triager's decision; nothing is published. Like a plan run, a missing or invalid
        file fails the run with the parser's fixed reason (or the agent's stated one)."""
        code, stopped = exit_status
        if stopped is not None:
            return _stopped(stopped, launch.timeout_s)
        hint = ARTIFACTS_HINT.format(run_id=context.run.id)
        try:
            decision = await asyncio.to_thread(
                read_triage, work.outbox / "triage.json", str(context.task.id)
            )
        except TriageFileError as error:
            return await self._no_output(context, work, launch, (code, str(error)))
        if code != 0:
            outcome = f"The sandbox failed (exit {code}); its triage was not used ({hint})."
            return SandboxResult(is_success=False, outcome=outcome)
        # Built from enums only: the triage's free text is untrusted.
        outcome = (
            f"Triage ready: {decision.type.value}, {decision.priority.value}/"
            f"{decision.severity.value}, {decision.verification.status.value}."
        )
        return SandboxResult(is_success=True, outcome=outcome, triage=decision)

    async def _review_result(
        self, context: RunContext, work: _Workdir, exit_status: tuple[int, Stop], launch: _Launch
    ) -> SandboxResult:
        """The reviewer's findings; nothing is published. Like a triage run, a missing or invalid
        file fails the run with the parser's fixed reason (or the agent's stated one)."""
        code, stopped = exit_status
        if stopped is not None:
            return _stopped(stopped, launch.timeout_s)
        hint = ARTIFACTS_HINT.format(run_id=context.run.id)
        try:
            draft = await asyncio.to_thread(
                read_review, work.outbox / "review.json", str(context.task.id)
            )
        except ReviewFileError as error:
            return await self._no_output(context, work, launch, (code, str(error)))
        if code != 0:
            outcome = f"The sandbox failed (exit {code}); its review was not used ({hint})."
            return SandboxResult(is_success=False, outcome=outcome)
        blockers = sum(1 for f in draft.findings if f.severity is ReviewSeverity.BLOCKER)
        nits = len(draft.findings) - blockers
        # Built from counts only: the review's free text is untrusted.
        outcome = (
            f"Review ready: {_plural(len(draft.findings), 'finding')}"
            f" ({_plural(blockers, 'blocker')}, {_plural(nits, 'nit')})."
        )
        return SandboxResult(is_success=True, outcome=outcome, review=draft)

    async def _no_output(
        self, context: RunContext, work: _Workdir, launch: _Launch, failure: tuple[int, str]
    ) -> SandboxResult:
        """A read-only run left no usable file: the agent's stated reason if it gave one (it
        stopped on purpose, e.g. blocked or out of budget), else the parser's fixed reason."""
        code, error = failure
        hint = ARTIFACTS_HINT.format(run_id=context.run.id)
        stated = await self._stated_reason(context, work, launch)
        if stated is not None:
            return SandboxResult(is_success=False, outcome=f"{stated} ({hint})")
        lead = f"The sandbox failed (exit {code}). " if code != 0 else ""
        return SandboxResult(is_success=False, outcome=f"{lead}{error} ({hint})")

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
        self,
        context: RunContext,
        work: _Workdir,
        snapshot: Snapshot,
        handoff: Handoff,
        *,
        push: bool = False,
    ) -> SandboxResult | str:
        """The published branch as a successful result, or why it couldn't be published. With
        `push` (a done handoff) and a pusher, the checked branch is also pushed to GitHub."""
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
        checked = await self._check_branch(context, branch, snapshot.base_sha)
        if isinstance(checked, str):
            return checked
        stat, tests = published.stat, handoff.tests
        # Built from counts only: the handoff's free text is untrusted and stays in `handoff`.
        # The reviewer's get_task reads these counts back (`parse_dev_run_outcome`).
        summary = DevRunSummary(
            branch=branch,
            files_changed=stat.files_changed,
            insertions=stat.insertions,
            deletions=stat.deletions,
            passed=tests.passed,
            failed=tests.failed,
            first_failing=tests.first_failing,
        )
        outcome = dev_run_outcome(summary, handoff.status)
        result = SandboxResult(
            is_success=True,
            outcome=outcome,
            branch=branch,
            base_sha=snapshot.base_sha,
            diff_stat=stat,
            handoff=handoff,
        )
        if not push or self._deps.pusher is None:
            return result
        trailers = PublishTrailers(
            task_id=task_id,
            passed=tests.passed,
            failed=tests.failed,
            diff_lines=changed_lines(checked),
        )
        return await self._push(self._deps.pusher, result, (branch, trailers))

    async def _push(
        self, pusher: BranchPusher, result: SandboxResult, what: tuple[str, PublishTrailers]
    ) -> SandboxResult:
        """Push the checked branch. Failure fails the run (the scratch branch stays); the
        message is git's verb and exit code only. The count parts stay for the reviewer."""
        branch, trailers = what
        try:
            pushed = await pusher.push(branch, trailers)
        except (GitError, ValueError) as error:
            target = github_branch(trailers.task_id)
            outcome = f"Could not push {target} to GitHub ({error}). {result.outcome}"
            return replace(result, is_success=False, outcome=outcome)
        note = f" Pushed {pushed.branch}; the PR opens from agent-pr.yml."
        return replace(result, outcome=f"{result.outcome}{note}")

    async def _check_branch(
        self, context: RunContext, branch: str, base_sha: str
    ) -> ChangeSet | str:
        """The Publisher's checks: the checked change set, or the refusal. On any violation (or
        if the branch can't be read) the branch is deleted and the refusal is the run's outcome
        (one line, redacted: paths are the agent's)."""
        host = self._deps.host
        changes: ChangeSet | None = None
        try:
            changes = await host.changes(base_sha, branch)
            found = await branch_violations(
                context, changes, self._deps.secret_filter, self._deps.redactor
            )
        except (GitError, ValueError):
            found = None
        if found == () and changes is not None:
            return changes
        # If deleting fails the run still fails; the branch stays only in the scratch remote.
        with contextlib.suppress(GitError, ValueError):
            await host.delete_branch(branch)
        if found is None:
            return "Could not check the agent branch, so it was not kept."
        text = _one_line(refusal(found))
        return await self._redact(text) or "The Publisher refused the branch (details withheld)."

    async def _stated_reason(
        self, context: RunContext, work: _Workdir, launch: _Launch
    ) -> str | None:
        """A failed handoff's reason (scrubbed, capped, redacted), or None if there is none."""
        try:
            handoff = await asyncio.to_thread(
                read_handoff, work.outbox / "handoff.json", str(context.task.id)
            )
        except HandoffError:
            return None
        if handoff.status == "done":
            return None
        return await self._reason(handoff, launch.secrets)

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


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _with_model(result: SandboxResult, model: str | None) -> SandboxResult:
    if result.triage is None:
        return replace(result, model=model)
    return replace(result, model=model, triage=replace(result.triage, model=model))


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
