"""DockerAgentRunner against a fake process runner: the docker argv keeps run.sh's hardening,
secrets travel only in the child env (never argv), and timeouts, kills, bad handoffs and
publishing all end the run the right way."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import uuid

import pytest

from app.adapters.agents.docker_runner import (
    HARDENING_FLAGS,
    NO_PROXY,
    USAGE_GRACE_S,
    DockerAgentRunner,
    SandboxConfig,
    SandboxDeps,
    SandboxResult,
    container_name,
)
from app.adapters.agents.tokens import CoordinatorTokensUnavailable
from app.domain.devboard.model import RunKind
from tests.unit.agents_runner.fakes import (
    BASE_SHA,
    PROXY_SECRET,
    RUN_ID,
    TASK_ID,
    FakeCodeHost,
    FakeCoordinatorTokens,
    FakePlanner,
    FakeProcessRunner,
    FakeTools,
    FakeUsageProbe,
    context,
    docker_env_flags,
    handoff,
    plan,
    writes,
)

WALL = 1_800_000_000.0
BRANCH = "agent/11111111-aaaaaaaa"


class Harness:
    def __init__(
        self,
        processes: FakeProcessRunner | None = None,
        *,
        timeout_s: int = 3600,
        host: FakeCodeHost | None = None,
        coordinator: FakeCoordinatorTokens | None = None,
    ) -> None:
        self.processes = processes or FakeProcessRunner()
        self.host = host or FakeCodeHost()
        self.coordinator = coordinator or FakeCoordinatorTokens()
        self.planner = FakePlanner()
        config = SandboxConfig(
            image="jobradar-sandbox-writer:dev", run_timeout_s=timeout_s, poll_s=0.01
        )
        deps = SandboxDeps(
            host=self.host,
            processes=self.processes,
            coordinator_tokens=self.coordinator,
            planner=self.planner,
            usage=FakeUsageProbe(),
        )
        self.runner = DockerAgentRunner(config, deps, PROXY_SECRET, wall=lambda: WALL)


def _env_value(argv: list[str], name: str) -> str | None:
    for flag in docker_env_flags(argv):
        key, _, value = flag.partition("=")
        if key == name:
            return value
    return None


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


async def test_argv_keeps_every_hardening_flag() -> None:
    # Drift against sandbox/run.sh (flags and env) is test_run_sh_parity.py.
    harness = Harness()
    await harness.runner.run(context(), FakeTools())
    argv = harness.processes.argv
    assert argv[:3] == ["docker", "run", "--rm"]
    for flag in HARDENING_FLAGS:
        joined = "\x00".join(argv)
        assert "\x00".join(flag) in joined, f"{flag} missing from the docker argv"
    assert "--privileged" not in argv
    assert not any("docker.sock" in arg for arg in argv)
    assert argv[-1] == "jobradar-sandbox-writer:dev"
    assert "--name" in argv
    assert argv[argv.index("--name") + 1] == container_name(TASK_ID, RUN_ID)


async def test_mounts_are_input_read_only_and_output_writable() -> None:
    harness = Harness()
    await harness.runner.run(context(), FakeTools())
    argv = harness.processes.argv
    mounts = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--mount"]
    assert len(mounts) == 2
    assert mounts[0].startswith("type=bind,source=")
    assert mounts[0].endswith(",target=/in,readonly")
    assert mounts[1].endswith(",target=/out")


async def test_secrets_travel_by_name_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-only-must-not-leak")
    monkeypatch.setenv("LLM_PROXY_SECRET", PROXY_SECRET.decode())
    coordinator = FakeCoordinatorTokens()
    harness = Harness(coordinator=coordinator)
    await harness.runner.run(context(), FakeTools())
    argv, env = harness.processes.argv, harness.processes.env
    flags = docker_env_flags(argv)
    assert "ANTHROPIC_AUTH_TOKEN" in flags
    assert "COORDINATOR_TOKEN" in flags
    assert env["ANTHROPIC_AUTH_TOKEN"].startswith("jrt2.")
    assert env["COORDINATOR_TOKEN"] == coordinator.token
    joined = " ".join(argv)
    for secret in (env["ANTHROPIC_AUTH_TOKEN"], env["COORDINATOR_TOKEN"], PROXY_SECRET.decode()):
        assert secret not in joined
    assert "ANTHROPIC_API_KEY" not in env
    assert "LLM_PROXY_SECRET" not in env
    assert PROXY_SECRET.decode() not in json.dumps(env)
    assert "sk-ant-test-only-must-not-leak" not in json.dumps(env)


async def test_env_points_at_the_proxies_and_carries_ids_and_budgets() -> None:
    harness = Harness()
    await harness.runner.run(context(plan_record=plan(minutes=30, budget_usd=2.0)), FakeTools())
    argv = harness.processes.argv
    assert _env_value(argv, "ANTHROPIC_BASE_URL") == "http://llm-proxy:8080"
    assert _env_value(argv, "COORDINATOR_URL") == "http://coordinator:8100"
    assert _env_value(argv, "TASK_ID") == str(TASK_ID)
    assert _env_value(argv, "RUN_ID") == str(RUN_ID)
    assert _env_value(argv, "HTTPS_PROXY") == "http://egress-proxy:3128"
    assert _env_value(argv, "NO_PROXY") == NO_PROXY == "localhost,127.0.0.1,llm-proxy,coordinator"
    assert _env_value(argv, "no_proxy") == NO_PROXY
    assert _env_value(argv, "AGENT_TIMEOUT_S") == "1800"
    # The USD budget travels in the llm-proxy token (jrt2), which enforces it.
    claims = json.loads(_b64d(harness.processes.env["ANTHROPIC_AUTH_TOKEN"].split(".")[1]))
    assert claims["usd_micro"] == 2_000_000
    assert argv[argv.index("--network") + 1] == "jobradar_sandbox"


async def test_llm_proxy_token_is_task_scoped_and_expires_with_the_run() -> None:
    harness = Harness(timeout_s=600)
    await harness.runner.run(context(plan_record=plan(minutes=30)), FakeTools())
    token = harness.processes.env["ANTHROPIC_AUTH_TOKEN"]
    prefix, payload, signature = token.split(".")
    expected = hmac.new(PROXY_SECRET, f"{prefix}.{payload}".encode(), hashlib.sha256).digest()
    assert prefix == "jrt2"
    assert hmac.compare_digest(_b64d(signature), expected)
    claims = json.loads(_b64d(payload))
    assert claims["t"] == str(TASK_ID)
    # Valid a little past the wall clock, so the spend can still be read after a kill.
    assert claims["exp"] == int(WALL) + 600 + USAGE_GRACE_S
    assert claims["usd_micro"] == 2_000_000
    assert _env_value(harness.processes.argv, "AGENT_TIMEOUT_S") == "600"


async def test_coordinator_token_is_minted_for_the_task_and_run() -> None:
    harness = Harness(timeout_s=900)
    await harness.runner.run(context(plan_record=plan(minutes=60)), FakeTools())
    assert isinstance(harness.coordinator, FakeCoordinatorTokens)
    assert harness.coordinator.calls == [(TASK_ID, RUN_ID, 900)]


async def test_success_publishes_the_branch_and_reports_it() -> None:
    harness = Harness()
    result = await harness.runner.run(context(), FakeTools())
    assert result.is_success
    assert isinstance(result, SandboxResult)
    assert len(harness.host.published) == 1
    bundle, source_ref, branch, base = harness.host.published[0]
    assert bundle.name == "result.bundle"
    assert source_ref == f"agent/{TASK_ID}"
    assert branch == BRANCH
    assert base == BASE_SHA
    assert result.branch == BRANCH
    assert result.diff_stat is not None
    assert result.diff_stat.files_changed == 1
    assert result.handoff is not None
    assert result.handoff.status == "done"
    assert BRANCH in result.outcome
    assert "+10 -2" in result.outcome
    # The agent's free text is untrusted: it is kept in the handoff, not in the outcome.
    assert "Stub agent" not in result.outcome


async def test_failed_handoff_is_published_but_fails_the_run() -> None:
    harness = Harness(FakeProcessRunner(write=writes(handoff(status="failed"))))
    result = await harness.runner.run(context(), FakeTools())
    assert not result.is_success
    assert harness.host.published
    assert "failed" in result.outcome


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("{not json", id="not-json"),
        pytest.param(handoff(task_id="someone-else"), id="wrong-task"),
        pytest.param({**handoff(), "status": "great"}, id="bad-status"),
        pytest.param({k: v for k, v in handoff().items() if k != "tests"}, id="missing-tests"),
        pytest.param({**handoff(), "tests": {"passed": "5"}}, id="wrong-type"),
        pytest.param(b"\xff\xfe\x00", id="not-utf8"),
        pytest.param(json.dumps({**handoff(), "summary": "x" * 70_000}), id="too-big"),
        pytest.param(None, id="missing"),
    ],
)
async def test_malformed_handoff_becomes_a_failed_run(
    payload: dict[str, object] | str | bytes | None,
) -> None:
    harness = Harness(FakeProcessRunner(write=writes(payload)))
    result = await harness.runner.run(context(), FakeTools())
    assert not result.is_success
    assert "handoff" in result.outcome.lower()
    assert harness.host.published == []


async def test_missing_result_bundle_fails_the_run() -> None:
    harness = Harness(FakeProcessRunner(write=writes(handoff(), bundle=False)))
    result = await harness.runner.run(context(), FakeTools())
    assert not result.is_success
    assert "bundle" in result.outcome.lower()
    assert harness.host.published == []


async def test_publish_failure_fails_the_run() -> None:
    harness = Harness(host=FakeCodeHost(fail_publish=True))
    result = await harness.runner.run(context(), FakeTools())
    assert not result.is_success
    assert "publish" in result.outcome.lower()


async def test_nonzero_exit_fails_the_run() -> None:
    harness = Harness(FakeProcessRunner(exit_code=1))
    result = await harness.runner.run(context(), FakeTools())
    assert not result.is_success
    assert "exit" in result.outcome.lower()
    assert harness.host.published == []


async def test_wall_clock_timeout_kills_the_container() -> None:
    processes = FakeProcessRunner(hang=True)
    harness = Harness(processes, timeout_s=0)
    result = await asyncio.wait_for(harness.runner.run(context(), FakeTools()), 5)
    assert not result.is_success
    assert "timed out" in result.outcome.lower()
    assert processes.kills == [["docker", "kill", container_name(TASK_ID, RUN_ID)]]


async def test_kill_switch_kills_the_container() -> None:
    processes = FakeProcessRunner(hang=True)
    harness = Harness(processes)
    tools = FakeTools()
    job = asyncio.ensure_future(harness.runner.run(context(), tools))
    await asyncio.sleep(0.05)
    tools.cancelled = True
    result = await asyncio.wait_for(job, 5)
    assert not result.is_success
    assert result.outcome == "Stopped."
    assert processes.kills == [["docker", "kill", container_name(TASK_ID, RUN_ID)]]
    assert harness.host.published == []


async def test_cancelling_the_run_task_kills_the_container() -> None:
    processes = FakeProcessRunner(hang=True)
    harness = Harness(processes)
    job = asyncio.ensure_future(harness.runner.run(context(), FakeTools()))
    await asyncio.sleep(0.05)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(job, 5)
    assert processes.kills == [["docker", "kill", container_name(TASK_ID, RUN_ID)]]
    assert all(not path.exists() for path in processes.seen_dirs)


@pytest.mark.parametrize("exit_code", [0, 1])
async def test_temp_dirs_are_removed(exit_code: int) -> None:
    processes = FakeProcessRunner(exit_code=exit_code)
    harness = Harness(processes)
    await harness.runner.run(context(), FakeTools())
    assert processes.seen_dirs
    assert all(not path.exists() for path in processes.seen_dirs)


async def test_plan_runs_go_to_the_planner_not_docker() -> None:
    harness = Harness()
    result = await harness.runner.run(context(RunKind.PLAN, with_plan=False), FakeTools())
    assert result.is_success
    assert harness.planner.calls == 1
    assert harness.processes.calls == []


@pytest.mark.parametrize("approved", [False, None])
async def test_dev_run_needs_an_approved_plan(approved: bool | None) -> None:
    harness = Harness()
    unapproved = context(plan_record=plan(approved=False))
    ctx = unapproved if approved is False else context(with_plan=False)
    result = await harness.runner.run(ctx, FakeTools())
    assert not result.is_success
    assert harness.processes.calls == []


class _NoCoordinatorTokens(FakeCoordinatorTokens):
    def mint(self, task_id: uuid.UUID, run_id: uuid.UUID, ttl: int) -> str:
        super().mint(task_id, run_id, ttl)
        raise CoordinatorTokensUnavailable("Could not mint the coordinator token.")


async def test_unmintable_coordinator_token_fails_the_run_without_docker() -> None:
    harness = Harness(coordinator=_NoCoordinatorTokens())
    result = await harness.runner.run(context(), FakeTools())
    assert not result.is_success
    assert "coordinator" in result.outcome.lower()
    assert harness.processes.calls == []


async def test_unmintable_llm_token_fails_the_run_without_docker() -> None:
    harness = Harness()
    harness.runner = DockerAgentRunner(
        SandboxConfig(image="img"),
        SandboxDeps(harness.host, harness.processes, harness.coordinator, harness.planner),
        b"too-short",
    )
    result = await harness.runner.run(context(), FakeTools())
    assert not result.is_success
    assert harness.processes.calls == []
    assert "too-short" not in result.outcome


async def test_nothing_secret_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    harness = Harness()
    await harness.runner.run(context(), FakeTools())
    env = harness.processes.env
    for secret in (env["ANTHROPIC_AUTH_TOKEN"], env["COORDINATOR_TOKEN"], PROXY_SECRET.decode()):
        assert secret not in caplog.text


def test_container_name_is_docker_safe() -> None:
    name = container_name(TASK_ID, RUN_ID)
    assert name == "jr-sandbox-11111111-aaaaaaaa"


def test_secret_is_not_in_the_runner_repr() -> None:
    harness = Harness()
    assert PROXY_SECRET.decode() not in repr(harness.runner)
    assert PROXY_SECRET.decode() not in str(vars(harness.runner).get("_config"))
