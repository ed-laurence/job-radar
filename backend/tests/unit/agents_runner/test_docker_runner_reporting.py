"""What a dev run reports back: the llm-proxy spend (or that it is unknown), the failure reason on
the card, the kept artifacts in `.artifacts/agent-runs/<run_id>/`, and the writer label."""

from __future__ import annotations

import dataclasses
import json
import os
import threading
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from app.adapters.agents.docker_runner import (
    AGENT_ROLE,
    ARTIFACTS_HINT,
    DockerAgentRunner,
    SandboxConfig,
    SandboxDeps,
    SandboxResult,
)
from app.adapters.agents.process import Completed
from app.adapters.agents.usage import DockerUsageProbe, ProxyUsage
from app.compose.devboard import build
from app.domain.devboard.model import NewStep, RunKind, RunRecord, RunStatus, StepKind
from app.domain.ports.devboard import RunFinish
from app.main import Container
from tests.unit.agents_runner.fakes import (
    NOW,
    PROXY_SECRET,
    RUN_ID,
    TASK_ID,
    USER_ID,
    FakeCodeHost,
    FakeCoordinatorTokens,
    FakePlanner,
    FakeProcessRunner,
    FakeRedactor,
    FakeTools,
    FakeUsageProbe,
    context,
    handoff,
    writes,
)
from tests.unit.agents_runner.test_tokens_settings_wiring import docker_settings

WALL = 1_800_000_000.0


def make_runner(
    processes: FakeProcessRunner | None = None,
    *,
    usage: FakeUsageProbe | None = None,
    artifacts: Path | None = None,
    redactor: FakeRedactor | None = None,
) -> tuple[DockerAgentRunner, FakeProcessRunner, FakeUsageProbe]:
    processes = processes or FakeProcessRunner()
    usage = usage or FakeUsageProbe()
    config = SandboxConfig(image="img:dev", poll_s=0.01, artifacts_root=artifacts)
    deps = SandboxDeps(
        host=FakeCodeHost(),
        processes=processes,
        coordinator_tokens=FakeCoordinatorTokens(),
        planner=FakePlanner(),
        usage=usage,
        redactor=redactor or FakeRedactor(),
    )
    return DockerAgentRunner(config, deps, PROXY_SECRET, wall=lambda: WALL), processes, usage


# ------------------------------------------------------------------ spend
async def test_spend_comes_from_llm_proxy_usage_with_the_task_token() -> None:
    runner, processes, usage = make_runner()
    result = await runner.run(context(), FakeTools())
    assert isinstance(result, SandboxResult)
    assert usage.tokens == [processes.env["ANTHROPIC_AUTH_TOKEN"]]
    assert result.cost_usd == pytest.approx(0.42)
    assert result.spend == ProxyUsage(
        usd_micro_used=420_000, usd_micro_budget=2_000_000, requests=7
    )
    assert "unknown" not in result.outcome.lower()


@pytest.mark.parametrize("exit_code", [0, 1])
async def test_spend_is_recorded_for_failed_runs_too(exit_code: int) -> None:
    runner, _, usage = make_runner(
        FakeProcessRunner(write=writes(handoff(status="failed")), exit_code=exit_code)
    )
    result = await runner.run(context(), FakeTools())
    assert not result.is_success
    assert len(usage.tokens) == 1
    assert result.cost_usd == pytest.approx(0.42)


async def test_unreachable_usage_is_unknown_spend_not_zero() -> None:
    runner, _, _ = make_runner(usage=FakeUsageProbe(answer=None))
    result = await runner.run(context(), FakeTools())
    assert isinstance(result, SandboxResult)
    assert result.spend is None
    assert "spend unknown" in result.outcome.lower()
    assert "$0.00" not in result.outcome


async def test_token_budget_is_the_plan_budget_in_micro_usd() -> None:
    runner, processes, _ = make_runner()
    await runner.run(context(), FakeTools())
    token = processes.env["ANTHROPIC_AUTH_TOKEN"]
    assert token.startswith("jrt2.")


# ------------------------------------------------------------------ failure reason
async def test_failed_handoff_summary_is_the_reason() -> None:
    summary = "Stopped: model budget used up after 41 requests."
    payload = {**handoff(status="failed"), "summary": summary}
    runner, _, _ = make_runner(FakeProcessRunner(write=writes(payload), exit_code=1))
    result = await runner.run(context(), FakeTools())
    assert not result.is_success
    assert result.outcome.startswith(summary)


async def test_failed_handoff_after_a_clean_exit_keeps_the_reason_and_the_branch() -> None:
    summary = "coordinator unreachable at http://coordinator:8100"
    payload = {**handoff(status="failed"), "summary": summary}
    runner, _, _ = make_runner(FakeProcessRunner(write=writes(payload)))
    result = await runner.run(context(), FakeTools())
    assert not result.is_success
    assert result.outcome.startswith(summary)
    assert "agent/11111111-aaaaaaaa" in result.outcome


async def test_reason_is_one_line_capped_and_redacted() -> None:
    redactor = FakeRedactor()
    summary = "line one\n\x1b[31mline two\x00 " + redactor.marker + " " + "x" * 2000
    payload = {**handoff(status="error"), "summary": summary[:2000]}
    runner, _, _ = make_runner(FakeProcessRunner(write=writes(payload)), redactor=redactor)
    result = await runner.run(context(), FakeTools())
    assert "\n" not in result.outcome
    assert "\x1b" not in result.outcome
    assert "\x00" not in result.outcome
    assert redactor.marker not in result.outcome
    assert result.outcome.startswith("line one line two [redacted]")
    assert len(result.outcome) <= 600


@pytest.mark.parametrize("exit_code", [0, 1])
@pytest.mark.parametrize(
    "payload", [pytest.param(None, id="missing"), pytest.param("{nope", id="invalid")]
)
async def test_no_handoff_points_at_the_artifacts(exit_code: int, payload: str | None) -> None:
    runner, _, _ = make_runner(FakeProcessRunner(write=writes(payload), exit_code=exit_code))
    result = await runner.run(context(), FakeTools())
    assert not result.is_success
    assert "the agent produced no handoff" in result.outcome.lower()
    assert f".artifacts/agent-runs/{RUN_ID}/" in result.outcome
    assert ARTIFACTS_HINT.format(run_id=RUN_ID) in result.outcome


async def test_successful_outcome_still_omits_the_free_text() -> None:
    runner, _, _ = make_runner()
    result = await runner.run(context(), FakeTools())
    assert result.is_success
    assert "Stub agent" not in result.outcome


# ------------------------------------------------------------------ artifacts
class _LeakyProcesses(FakeProcessRunner):
    """A container that echoes its tokens everywhere it can."""

    async def run(
        self,
        argv: Sequence[str],
        env: Mapping[str, str],
        *,
        stop: threading.Event,
        kill_argv: Sequence[str],
        output: Path | None = None,
    ) -> int:
        leak = f"{env['ANTHROPIC_AUTH_TOKEN']} {env['COORDINATOR_TOKEN']} PII-MARKER-0001"
        self.console = f"console {leak}\n".encode()
        inner = self.write

        def write(out: Path) -> None:
            inner(out)
            (out / "agent.log").write_text(f"agent {leak}\n", encoding="utf-8")
            (out / "steps.jsonl").write_text(json.dumps({"s": leak}) + "\n", encoding="utf-8")

        self.write = write
        return await super().run(argv, env, stop=stop, kill_argv=kill_argv, output=output)


def _artifact_texts(folder: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in folder.iterdir()}


async def test_artifacts_are_kept_per_run(tmp_path: Path) -> None:
    root = tmp_path / "agent-runs"

    def write(out: Path) -> None:
        writes(handoff())(out)
        (out / "agent.log").write_text("agent says hi\n", encoding="utf-8")
        (out / "steps.jsonl").write_text('{"kind":"thought"}\n', encoding="utf-8")

    processes = FakeProcessRunner(write=write)
    runner, _, _ = make_runner(processes, artifacts=root)
    await runner.run(context(), FakeTools())
    texts = _artifact_texts(root / str(RUN_ID))
    assert set(texts) == {"handoff.json", "agent.log", "steps.jsonl", "runner.log"}
    assert json.loads(texts["handoff.json"])["task_id"] == str(TASK_ID)
    assert "agent says hi" in texts["agent.log"]
    assert "sandbox: done" in texts["runner.log"]
    # The result bundle is published, not kept.
    assert not (root / str(RUN_ID) / "result.bundle").exists()


async def test_artifacts_are_kept_when_the_container_fails(tmp_path: Path) -> None:
    root = tmp_path / "agent-runs"
    runner, _, _ = make_runner(FakeProcessRunner(write=writes(None), exit_code=3), artifacts=root)
    await runner.run(context(), FakeTools())
    assert (root / str(RUN_ID) / "runner.log").is_file()


async def test_artifacts_never_hold_tokens_and_are_redacted(tmp_path: Path) -> None:
    root = tmp_path / "agent-runs"
    processes = _LeakyProcesses()
    runner, _, _ = make_runner(processes, artifacts=root)
    await runner.run(context(), FakeTools())
    env = processes.env
    texts = _artifact_texts(root / str(RUN_ID))
    assert {"runner.log", "agent.log", "steps.jsonl"} <= set(texts)
    for name, text in texts.items():
        assert env["ANTHROPIC_AUTH_TOKEN"] not in text, name
        assert env["COORDINATOR_TOKEN"] not in text, name
        assert PROXY_SECRET.decode() not in text, name
        assert "PII-MARKER-0001" not in text, name


async def test_only_the_last_20_runs_are_kept(tmp_path: Path) -> None:
    root = tmp_path / "agent-runs"
    root.mkdir()
    old = [uuid.UUID(int=i + 1) for i in range(25)]
    for i, run_id in enumerate(old):
        folder = root / str(run_id)
        folder.mkdir()
        (folder / "runner.log").write_text("old", encoding="utf-8")
        os.utime(folder, (1_000_000 + i, 1_000_000 + i))
    stray = root / "not-a-run"
    stray.mkdir()
    os.utime(stray, (1, 1))
    runner, _, _ = make_runner(artifacts=root)
    await runner.run(context(), FakeTools())
    kept = {p.name for p in root.iterdir()}
    assert str(RUN_ID) in kept
    assert kept - {"not-a-run"} == {str(RUN_ID)} | {str(r) for r in old[-19:]}
    assert "not-a-run" in kept, "only run folders are pruned"


async def test_no_artifacts_root_keeps_nothing(tmp_path: Path) -> None:
    runner, _, _ = make_runner(artifacts=None)
    result = await runner.run(context(), FakeTools())
    assert result.is_success


# ------------------------------------------------------------------ usage probe
class _Capture:
    def __init__(self, completed: Completed | Exception) -> None:
        self.completed = completed
        self.calls: list[tuple[list[str], dict[str, str], float]] = []

    async def __call__(
        self, argv: Sequence[str], *, env: Mapping[str, str], cwd: Path | None, timeout_s: float
    ) -> Completed:
        assert cwd is None
        self.calls.append((list(argv), dict(env), timeout_s))
        if isinstance(self.completed, Exception):
            raise self.completed
        return self.completed


def _usage_body(**overrides: object) -> bytes:
    body = {"usd_micro_used": 1_234_567, "usd_micro_budget": 2_000_000, "requests": 12}
    return json.dumps({**body, **overrides}).encode()


async def test_usage_probe_asks_llm_proxy_on_the_sandbox_network() -> None:
    capture = _Capture(Completed(code=0, stdout=_usage_body()))
    probe = DockerUsageProbe(SandboxConfig(image="img:dev"), {"PATH": "/bin"}, capture=capture)
    usage = await probe.usage("jrt2.tok.sig")
    assert usage == ProxyUsage(usd_micro_used=1_234_567, usd_micro_budget=2_000_000, requests=12)
    argv, env, timeout_s = capture.calls[0]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[argv.index("--network") + 1] == "jobradar_sandbox"
    assert "--read-only" in argv
    assert "--cap-drop" in argv
    assert "LLM_PROXY_TOKEN" in argv
    assert "jrt2.tok.sig" not in " ".join(argv)
    assert env["LLM_PROXY_TOKEN"] == "jrt2.tok.sig"
    assert "USAGE_URL=http://llm-proxy:8080/v1/usage" in argv
    assert timeout_s <= 60


@pytest.mark.parametrize(
    "completed",
    [
        pytest.param(Completed(code=1, stdout=b""), id="exit"),
        pytest.param(Completed(code=0, stdout=b"not json"), id="not-json"),
        pytest.param(Completed(code=0, stdout=_usage_body(requests=-1)), id="negative"),
        pytest.param(Completed(code=0, stdout=_usage_body(usd_micro_used="1")), id="type"),
        pytest.param(Completed(code=0, stdout=b"{" + b" " * 70_000 + b"}"), id="too-big"),
        pytest.param(TimeoutError(), id="timeout"),
        pytest.param(OSError("docker missing"), id="no-docker"),
    ],
)
async def test_usage_probe_failure_is_unknown(completed: Completed | Exception) -> None:
    probe = DockerUsageProbe(SandboxConfig(image="img:dev"), {}, capture=_Capture(completed))
    assert await probe.usage("jrt2.tok.sig") is None


# ------------------------------------------------------------------ writer label
def _dev_run(kind: RunKind = RunKind.DEV) -> RunRecord:
    return RunRecord(
        id=uuid.uuid4(),
        task_id=uuid.uuid4(),
        kind=kind,
        status=RunStatus.QUEUED,
        created_at=NOW,
        agent="dev-lead",
    )


async def test_docker_dev_runs_are_labelled_with_the_writer_role(
    container: Container, tmp_path: Path
) -> None:
    settings = docker_settings(agent_remote_path=tmp_path / "remote")
    runtime = build(dataclasses.replace(container, settings=settings))
    runs = runtime.stores.runs
    dev, plan_run = _dev_run(), _dev_run(RunKind.PLAN)
    await runs.insert(USER_ID, dev)
    await runs.insert(USER_ID, plan_run)
    stored = await runs.get(USER_ID, dev.id)
    assert stored is not None
    assert stored.agent == AGENT_ROLE == "writer"
    planned = await runs.get(USER_ID, plan_run.id)
    assert planned is not None
    assert planned.agent == "dev-lead"
    # The final step carries the stored label, not the caller's stale record.
    finish = RunFinish(status=RunStatus.FAILED, outcome="x", at=NOW)
    final = NewStep(StepKind.STATUS, "Run failed: x", "dev-lead")
    await runs.finish(USER_ID, dev.id, finish, final)
    steps = await runs.steps_after(USER_ID, dev.id, 0, 10)
    assert [s.agent for s in steps] == ["writer"]


async def test_simulated_dev_runs_keep_their_label(container: Container) -> None:
    runtime = build(container)
    run = _dev_run()
    await runtime.stores.runs.insert(USER_ID, run)
    stored = await runtime.stores.runs.get(USER_ID, run.id)
    assert stored is not None
    assert stored.agent == "dev-lead"
