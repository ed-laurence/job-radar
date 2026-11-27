"""Plan runs in the sandbox (the sandboxed dev-lead): same image, hardening and env as dev runs
plus `AGENT_MODE=plan`, budget and timeout from config (there is no plan yet), nothing
published, and `/out/plan.json` read strictly into the draft. With a `planner` given, plan
runs stay on it (the simulation)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from app.adapters.agents.docker_runner import (
    HARDENING_FLAGS,
    DockerAgentRunner,
    SandboxConfig,
    SandboxDeps,
    SandboxResult,
)
from app.domain.devboard.model import RunKind
from tests.unit.agents_runner.fakes import (
    PROXY_SECRET,
    RUN_ID,
    TASK_ID,
    FakeCodeHost,
    FakeCoordinatorTokens,
    FakeProcessRunner,
    FakeTools,
    FakeUsageProbe,
    Writer,
    context,
    docker_env_flags,
    handoff,
)
from tests.unit.agents_runner.fakes import plan as approved_plan
from tests.unit.agents_runner.test_plan_file import plan_file

WALL = 1_800_000_000.0
IMAGE = "jobradar-sandbox-writer:dev"


def writes_plan(payload: dict[str, object] | str | None) -> Writer:
    def write(out: Path) -> None:
        if isinstance(payload, dict):
            (out / "plan.json").write_text(json.dumps(payload), encoding="utf-8")
        elif isinstance(payload, str):
            (out / "plan.json").write_text(payload, encoding="utf-8")

    return write


class PlanHarness:
    def __init__(
        self,
        processes: FakeProcessRunner | None = None,
        *,
        artifacts: Path | None = None,
        run_timeout_s: int = 3600,
    ) -> None:
        self.processes = processes or FakeProcessRunner(write=writes_plan(plan_file()))
        self.host = FakeCodeHost()
        self.usage = FakeUsageProbe()
        config = SandboxConfig(
            image=IMAGE,
            run_timeout_s=run_timeout_s,
            poll_s=0.01,
            plan_budget_usd=0.5,
            plan_timeout_s=600,
            artifacts_root=artifacts,
        )
        deps = SandboxDeps(
            host=self.host,
            processes=self.processes,
            coordinator_tokens=FakeCoordinatorTokens(),
            usage=self.usage,
        )
        self.runner = DockerAgentRunner(config, deps, PROXY_SECRET, wall=lambda: WALL)

    async def run(self) -> SandboxResult:
        result = await self.runner.run(context(RunKind.PLAN, with_plan=False), FakeTools())
        assert isinstance(result, SandboxResult)
        return result


def _env(argv: list[str]) -> dict[str, str]:
    pairs = (flag.partition("=") for flag in docker_env_flags(argv))
    return {key: value for key, sep, value in pairs if sep}


def _claims(token: str) -> dict[str, object]:
    payload = token.split(".")[1]
    claims: dict[str, object] = json.loads(base64.urlsafe_b64decode(payload + "=" * 4))
    return claims


async def test_plan_runs_use_the_sandbox_with_plan_mode() -> None:
    harness = PlanHarness()
    result = await harness.run()
    assert result.is_success, result.outcome
    argv = harness.processes.argv
    joined = "\x00".join(argv)
    for flag in HARDENING_FLAGS:
        assert "\x00".join(flag) in joined
    assert argv[-1] == IMAGE
    env = _env(argv)
    assert env["AGENT_MODE"] == "plan"
    assert env["TASK_ID"] == str(TASK_ID)
    assert env["RUN_ID"] == str(RUN_ID)
    assert "ANTHROPIC_AUTH_TOKEN" in docker_env_flags(argv)
    assert "COORDINATOR_TOKEN" in docker_env_flags(argv)


async def test_dev_runs_do_not_get_plan_mode() -> None:
    processes = FakeProcessRunner()
    harness = PlanHarness(processes)
    await harness.runner.run(context(plan_record=approved_plan()), FakeTools())
    assert "AGENT_MODE" not in _env(processes.argv)


async def test_plan_budget_and_timeout_come_from_config() -> None:
    harness = PlanHarness()
    await harness.run()
    env = _env(harness.processes.argv)
    assert env["AGENT_TIMEOUT_S"] == "600"
    claims = _claims(harness.processes.env["ANTHROPIC_AUTH_TOKEN"])
    assert claims["usd_micro"] == 500_000


async def test_the_plan_timeout_never_exceeds_the_run_cap() -> None:
    harness = PlanHarness(run_timeout_s=300)
    await harness.run()
    assert _env(harness.processes.argv)["AGENT_TIMEOUT_S"] == "300"


async def test_the_plan_becomes_the_draft_and_nothing_is_published() -> None:
    harness = PlanHarness()
    result = await harness.run()
    assert result.plan is not None
    assert [s.id for s in result.plan.subtasks] == ["t0", "s1"]
    assert result.plan.tests_to_change == ("backend/tests/unit/test_y.py",)
    assert harness.host.published == []
    assert result.branch is None
    assert "2 subtasks" in result.outcome
    # Spend comes from llm-proxy, as for dev runs.
    assert result.cost_usd == pytest.approx(0.42)
    assert harness.usage.tokens == [harness.processes.env["ANTHROPIC_AUTH_TOKEN"]]


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(None, id="missing"),
        pytest.param("{not json", id="not-json"),
        pytest.param(plan_file(extra=1), id="unknown-key"),
        pytest.param(plan_file(task_id="someone-else"), id="wrong-task"),
    ],
)
async def test_a_missing_or_invalid_plan_fails_the_run(
    payload: dict[str, object] | str | None,
) -> None:
    harness = PlanHarness(FakeProcessRunner(write=writes_plan(payload)))
    result = await harness.run()
    assert not result.is_success
    assert result.plan is None
    assert f".artifacts/agent-runs/{RUN_ID}/" in result.outcome
    assert "plan" in result.outcome.lower()
    assert "someone-else" not in result.outcome


async def test_a_failed_sandbox_fails_the_run_even_with_a_plan() -> None:
    harness = PlanHarness(FakeProcessRunner(write=writes_plan(plan_file()), exit_code=2))
    result = await harness.run()
    assert not result.is_success
    assert result.plan is None
    assert "exit 2" in result.outcome


async def test_a_timed_out_plan_run_is_killed() -> None:
    harness = PlanHarness(FakeProcessRunner(hang=True), run_timeout_s=0)
    result = await harness.run()
    assert not result.is_success
    assert "Timed out" in result.outcome
    assert harness.processes.kills


async def test_the_plan_file_is_kept_with_the_run_logs(tmp_path: Path) -> None:
    harness = PlanHarness(artifacts=tmp_path)
    await harness.run()
    assert (tmp_path / str(RUN_ID) / "plan.json").is_file()


def test_plan_runs_are_labelled_dev_lead() -> None:
    harness = PlanHarness()
    assert harness.runner.labels[RunKind.PLAN] == "dev-lead"
    assert harness.runner.labels[RunKind.DEV] == "writer"


def writes_failed_handoff(summary: str) -> Writer:
    """The planner stopped without a plan and said why in its handoff."""

    def write(out: Path) -> None:
        payload = handoff(status="failed")
        payload["summary"] = summary
        (out / "handoff.json").write_text(json.dumps(payload), encoding="utf-8")

    return write


async def test_a_planner_that_stops_without_a_plan_gives_its_reason() -> None:
    reason = "Blocked: BoardMetrics.test.tsx asserts the rate; needs a modify-tests label."
    harness = PlanHarness(FakeProcessRunner(write=writes_failed_handoff(reason)))
    result = await harness.run()
    assert not result.is_success
    assert result.plan is None
    assert result.outcome.startswith(reason)
    assert f".artifacts/agent-runs/{RUN_ID}/" in result.outcome
