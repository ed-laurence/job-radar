"""Review runs in the sandbox (the read-only reviewer, `AGENT_MODE=review`): budget, wall clock
and model from config, the input bundle is the card's branch head with the card's base
(`AGENT_BASE_SHA`), nothing is published, and `/out/review.json` is read strictly into the
review draft. Also the review settings."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.adapters.agents.docker_runner import (
    DockerAgentRunner,
    SandboxConfig,
    SandboxDeps,
    SandboxResult,
)
from app.domain.devboard.model import ReviewRecord, ReviewSeverity, ReviewVerdict, RunKind
from app.settings import Settings
from tests.unit.agents_runner.fakes import (
    PROXY_SECRET,
    RUN_ID,
    TASK_ID,
    CardState,
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
from tests.unit.agents_runner.test_review_file import finding, review_file

WALL = 1_800_000_000.0
CARD_BRANCH = "agent/11111111-99999999"
CARD_BASE = "c" * 40
CARD = CardState(branch=CARD_BRANCH, base_sha=CARD_BASE)


def writes_review(payload: dict[str, object] | str | None) -> Writer:
    def write(out: Path) -> None:
        if isinstance(payload, dict):
            (out / "review.json").write_text(json.dumps(payload), encoding="utf-8")
        elif isinstance(payload, str):
            (out / "review.json").write_text(payload, encoding="utf-8")

    return write


class ReviewHarness:
    def __init__(
        self,
        processes: FakeProcessRunner | None = None,
        *,
        artifacts: Path | None = None,
        run_timeout_s: int = 3600,
        host: FakeCodeHost | None = None,
    ) -> None:
        self.processes = processes or FakeProcessRunner(write=writes_review(review_file()))
        self.host = host or FakeCodeHost()
        config = SandboxConfig(
            image="jobradar-sandbox-writer:dev",
            run_timeout_s=run_timeout_s,
            poll_s=0.01,
            review_budget_usd=0.75,
            review_timeout_s=480,
            review_model="claude-opus-5-5",
            artifacts_root=artifacts,
        )
        deps = SandboxDeps(
            host=self.host,
            processes=self.processes,
            coordinator_tokens=FakeCoordinatorTokens(),
            usage=FakeUsageProbe(),
        )
        self.runner = DockerAgentRunner(config, deps, PROXY_SECRET, wall=lambda: WALL)

    async def run(self, card: CardState | None = CARD) -> SandboxResult:
        result = await self.runner.run(context(RunKind.REVIEW, card=card), FakeTools())
        assert isinstance(result, SandboxResult)
        return result


def _env(argv: list[str]) -> dict[str, str]:
    pairs = (flag.partition("=") for flag in docker_env_flags(argv))
    return {key: value for key, sep, value in pairs if sep}


def _claims(token: str) -> dict[str, object]:
    payload = token.split(".")[1]
    claims: dict[str, object] = json.loads(base64.urlsafe_b64decode(payload + "=" * 4))
    return claims


async def test_review_runs_use_review_mode_on_the_review_model() -> None:
    harness = ReviewHarness()
    result = await harness.run()
    assert result.is_success, result.outcome
    env = _env(harness.processes.argv)
    assert env["AGENT_MODE"] == "review"
    assert env["AGENT_MODEL"] == "claude-opus-5-5"
    assert env["AGENT_BASE_SHA"] == CARD_BASE
    assert result.model == "claude-opus-5-5"


async def test_review_budget_and_timeout_come_from_config() -> None:
    harness = ReviewHarness()
    await harness.run()
    assert _env(harness.processes.argv)["AGENT_TIMEOUT_S"] == "480"
    claims = _claims(harness.processes.env["ANTHROPIC_AUTH_TOKEN"])
    assert claims["usd_micro"] == 750_000


async def test_the_review_timeout_never_exceeds_the_run_cap() -> None:
    harness = ReviewHarness(run_timeout_s=300)
    await harness.run()
    assert _env(harness.processes.argv)["AGENT_TIMEOUT_S"] == "300"


async def test_the_input_is_the_card_branch_head_not_a_working_tree_snapshot() -> None:
    harness = ReviewHarness()
    await harness.run()
    assert harness.host.branch_bundles == [(CARD_BRANCH, CARD_BASE)]
    assert harness.host.snapshots == 0


async def test_the_review_becomes_the_draft_and_nothing_is_published() -> None:
    harness = ReviewHarness()
    result = await harness.run()
    assert result.review is not None
    assert [f.severity for f in result.review.findings] == [
        ReviewSeverity.BLOCKER,
        ReviewSeverity.NIT,
    ]
    assert harness.host.published == []
    assert harness.host.deleted == []
    assert result.branch is None
    # Built from counts only: the review's free text is untrusted.
    assert result.outcome.startswith("Review ready: 2 findings (1 blocker, 1 nit).")
    assert "route" not in result.outcome


async def test_a_card_without_a_branch_is_not_reviewed() -> None:
    harness = ReviewHarness()
    result = await harness.run(card=None)
    assert not result.is_success
    assert "no published branch" in result.outcome
    assert harness.processes.calls == []


async def test_a_branch_that_cannot_be_bundled_fails_the_run() -> None:
    harness = ReviewHarness(host=FakeCodeHost(fail_branch_bundle=True))
    result = await harness.run()
    assert not result.is_success
    assert result.outcome.startswith("Could not bundle the agent branch.")
    assert harness.processes.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(None, id="missing"),
        pytest.param("{not json", id="not-json"),
        pytest.param(review_file(extra=1), id="unknown-key"),
        pytest.param(review_file(task_id="someone-else"), id="wrong-task"),
        pytest.param(review_file(findings=[finding()] * 11), id="too-many"),
    ],
)
async def test_a_missing_or_invalid_review_fails_the_run(
    payload: dict[str, object] | str | None,
) -> None:
    harness = ReviewHarness(FakeProcessRunner(write=writes_review(payload)))
    result = await harness.run()
    assert not result.is_success
    assert result.review is None
    assert f".artifacts/agent-runs/{RUN_ID}/" in result.outcome
    assert "review" in result.outcome.lower()
    assert "someone-else" not in result.outcome


async def test_a_reviewer_that_stops_without_a_review_gives_its_reason() -> None:
    reason = "Stopped: model budget used up after 6 turns ($0.99 of $1.00)."

    def write(out: Path) -> None:
        payload = handoff(status="failed")
        payload["summary"] = reason
        (out / "handoff.json").write_text(json.dumps(payload), encoding="utf-8")

    harness = ReviewHarness(FakeProcessRunner(write=write))
    result = await harness.run()
    assert not result.is_success
    assert result.outcome.startswith(reason)
    assert f".artifacts/agent-runs/{RUN_ID}/" in result.outcome


async def test_a_failed_sandbox_fails_the_run_even_with_a_review() -> None:
    harness = ReviewHarness(FakeProcessRunner(write=writes_review(review_file()), exit_code=2))
    result = await harness.run()
    assert not result.is_success
    assert result.review is None
    assert "exit 2" in result.outcome


async def test_a_timed_out_review_run_is_killed() -> None:
    harness = ReviewHarness(FakeProcessRunner(hang=True), run_timeout_s=0)
    result = await harness.run()
    assert not result.is_success
    assert "Timed out" in result.outcome
    assert harness.processes.kills


async def test_the_review_file_is_kept_with_the_run_logs(tmp_path: Path) -> None:
    harness = ReviewHarness(artifacts=tmp_path)
    await harness.run()
    assert (tmp_path / str(RUN_ID) / "review.json").is_file()


def test_review_runs_are_labelled_reviewer() -> None:
    assert ReviewHarness().runner.labels[RunKind.REVIEW] == "reviewer"


async def test_a_round_two_review_still_starts_from_the_card_branch() -> None:
    previous = ReviewRecord(
        id=uuid.uuid4(),
        task_id=TASK_ID,
        run_id=uuid.uuid4(),
        round=1,
        verdict=ReviewVerdict.CHANGES_REQUESTED,
        summary="One blocker.",
        findings=(),
        created_at=datetime(2026, 1, 5, tzinfo=UTC),
    )
    harness = ReviewHarness()
    result = await harness.runner.run(
        context(RunKind.REVIEW, card=CARD, review=previous), FakeTools()
    )
    assert result.is_success
    assert harness.host.branch_bundles == [(CARD_BRANCH, CARD_BASE)]
    assert harness.host.published == []


# ------------------------------------------------------------------ settings


def test_review_defaults() -> None:
    settings = Settings(environment="test")
    assert settings.agent_review_budget_usd == 1.0
    assert settings.agent_review_timeout_s == 600
    assert settings.agent_review_model == "claude-sonnet-5-5"


@pytest.mark.parametrize(
    "overrides",
    [
        {"agent_review_budget_usd": 0},
        {"agent_review_budget_usd": 21},
        {"agent_review_timeout_s": 30},
        {"agent_review_timeout_s": 7201},
        {"agent_review_model": ""},
        {"agent_review_model": "x" * 101},
    ],
)
def test_review_settings_are_bounded(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="validation"):
        Settings.model_validate({"environment": "test", **overrides})
