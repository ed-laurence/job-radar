"""Drift test for review and rework runs: their `docker run` (with `AGENT_MODE=review`,
`AGENT_MODEL` and `AGENT_BASE_SHA`) is still exactly `sandbox/run.sh`'s invocation."""

from __future__ import annotations

from app.adapters.agents.docker_runner import DockerAgentRunner, SandboxConfig, SandboxDeps
from app.domain.devboard.model import RunKind
from app.domain.ports.devboard import RunContext
from tests.unit.agents_runner.fakes import (
    PROXY_SECRET,
    CardState,
    FakeCodeHost,
    FakeCoordinatorTokens,
    FakeProcessRunner,
    FakeTools,
    FakeUsageProbe,
    context,
)
from tests.unit.agents_runner.test_docker_publish_checks import BLOCKERS
from tests.unit.agents_runner.test_run_sh_parity import drift, parse_run_sh, parse_runner_argv

CARD = CardState(branch="agent/11111111-99999999", base_sha="c" * 40)


async def _argv(built: RunContext) -> tuple[list[str], str]:
    processes = FakeProcessRunner()
    config = SandboxConfig(image="jobradar-sandbox-writer:dev", poll_s=0.01)
    deps = SandboxDeps(
        host=FakeCodeHost(),
        processes=processes,
        coordinator_tokens=FakeCoordinatorTokens(),
        usage=FakeUsageProbe(),
    )
    await DockerAgentRunner(config, deps, PROXY_SECRET).run(built, FakeTools())
    return processes.argv, config.image


async def test_a_review_run_matches_run_sh() -> None:
    argv, image = await _argv(context(RunKind.REVIEW, card=CARD))
    ours = parse_runner_argv(argv, image)
    assert ours.fixed_env["AGENT_MODE"] == "review"
    assert ours.fixed_env["AGENT_BASE_SHA"] == "c" * 40
    assert drift(parse_run_sh(), ours) == []


async def test_a_rework_run_matches_run_sh() -> None:
    argv, image = await _argv(context(card=CARD, review=BLOCKERS))
    ours = parse_runner_argv(argv, image)
    assert ours.fixed_env["AGENT_BASE_SHA"] == "c" * 40
    assert drift(parse_run_sh(), ours) == []
