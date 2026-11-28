"""Dev runs after publishing: the Publisher's checks (domain rules plus PUB-SECRET through the
redaction filter's fast rules) fail the run and delete the branch on any violation; a clean
branch sets `branch` and `base_sha`. Rework dev runs (a review asked for changes and the card has
a branch) start from the card's branch head with the card's base."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.adapters.agents.docker_runner import (
    DockerAgentRunner,
    SandboxConfig,
    SandboxDeps,
    SandboxResult,
)
from app.domain.devboard.model import (
    ReviewFinding,
    ReviewRecord,
    ReviewSeverity,
    ReviewVerdict,
    RunKind,
    Subtask,
)
from app.domain.ports.devboard import ChangeSet, FileChange
from tests.unit.agents_runner.fakes import (
    BASE_SHA,
    PROXY_SECRET,
    TASK_ID,
    CardState,
    FakeCodeHost,
    FakeCoordinatorTokens,
    FakeProcessRunner,
    FakeRedactor,
    FakeTools,
    FakeUsageProbe,
    context,
    docker_env_flags,
    plan,
)

WALL = 1_800_000_000.0
BRANCH = "agent/11111111-aaaaaaaa"
FEATURE = "web/src/features/board/Card.tsx"
LEASED = plan(subtasks=(Subtask("s1", "Badge", "frontend-engineer", ("web/src/features/board/",)),))
SECRET_MARKER = "AKIA" + "Z" * 16


class FakeSecretFilter:
    """Stands in for the redaction filter's fast rules (secret patterns + denylist)."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def redact_fast(self, text: str, *, is_complete: bool = False) -> str:
        assert is_complete
        self.seen.append(text)
        return text.replace(SECRET_MARKER, "[redacted]")


class Harness:
    def __init__(self, change_set: ChangeSet, *, secrets: FakeSecretFilter | None = None) -> None:
        self.host = FakeCodeHost(change_set=change_set)
        self.processes = FakeProcessRunner()
        self.redactor = FakeRedactor()
        deps = SandboxDeps(
            host=self.host,
            processes=self.processes,
            coordinator_tokens=FakeCoordinatorTokens(),
            usage=FakeUsageProbe(),
            redactor=self.redactor,
            secret_filter=secrets,
        )
        config = SandboxConfig(image="jobradar-sandbox-writer:dev", poll_s=0.01)
        self.runner = DockerAgentRunner(config, deps, PROXY_SECRET, wall=lambda: WALL)

    async def run(
        self, *, card: CardState | None = None, review: ReviewRecord | None = None
    ) -> SandboxResult:
        built = context(plan_record=LEASED, card=card, review=review)
        result = await self.runner.run(built, FakeTools())
        assert isinstance(result, SandboxResult)
        return result


def _changes(*files: FileChange, added: tuple[tuple[str, str], ...] = ()) -> ChangeSet:
    return ChangeSet(files=files, added_lines=added)


async def test_a_clean_branch_is_kept_with_its_branch_and_base() -> None:
    harness = Harness(_changes(FileChange(FEATURE, "M", 3, 1), added=((FEATURE, "const a = 1;"),)))
    result = await harness.run()
    assert result.is_success, result.outcome
    assert (result.branch, result.base_sha) == (BRANCH, BASE_SHA)
    assert harness.host.changes_asked == [(BASE_SHA, BRANCH)]
    assert harness.host.deleted == []


async def test_violations_delete_the_branch_and_fail_the_run_with_the_list() -> None:
    outside = "web/src/features/settings/Page.tsx"
    changes = _changes(
        FileChange(outside, "M", 1, 0),
        FileChange(FEATURE, "M", 1, 0),
        added=((FEATURE, "x = 1  # noqa"),),
    )
    harness = Harness(changes)
    result = await harness.run()
    assert not result.is_success
    assert harness.host.deleted == [BRANCH]
    assert result.branch is None
    assert result.outcome.startswith("The Publisher refused the branch:")
    assert f"PUB-LEASE: {outside} — outside the run's leases" in result.outcome
    assert f"PUB-PRAGMA: {FEATURE} — adds `noqa`" in result.outcome


async def test_at_most_ten_violations_are_shown() -> None:
    files = tuple(FileChange(f"docs/other/f{index}.md", "A", 1, 0) for index in range(15))
    harness = Harness(_changes(*files))
    result = await harness.run()
    assert not result.is_success
    assert result.outcome.count("PUB-") == 10
    assert "and 20 more" in result.outcome  # 15 x (PUB-LEASE + PUB-SCOPE) = 30


async def test_pub_secret_flags_added_lines_the_filter_changes_without_quoting_them() -> None:
    secrets = FakeSecretFilter()
    added = ((FEATURE, f"const key = '{SECRET_MARKER}';"), (FEATURE, "const b = 2;"))
    harness = Harness(_changes(FileChange(FEATURE, "M", 2, 0), added=added), secrets=secrets)
    result = await harness.run()
    assert not result.is_success
    assert f"PUB-SECRET: {FEATURE} — 1 added line matches the secret rules" in result.outcome
    assert SECRET_MARKER not in result.outcome
    assert harness.host.deleted == [BRANCH]
    assert len(secrets.seen) == 2


async def test_the_violation_list_passes_the_redaction_filter() -> None:
    marked = f"web/src/features/settings/{FakeRedactor().marker}.tsx"
    harness = Harness(_changes(FileChange(marked, "A", 1, 0)))
    result = await harness.run()
    assert FakeRedactor().marker not in result.outcome
    assert "[redacted]" in result.outcome


# ------------------------------------------------------------------ rework runs
CARD_BRANCH = "agent/11111111-99999999"
CARD_BASE = "c" * 40
BLOCKERS = ReviewRecord(
    id=uuid.uuid4(),
    task_id=TASK_ID,
    run_id=uuid.uuid4(),
    round=1,
    verdict=ReviewVerdict.CHANGES_REQUESTED,
    summary="One blocker.",
    findings=(
        ReviewFinding(ReviewSeverity.BLOCKER, FEATURE, "Rule in the route.", rubric_id="ARCH-03"),
    ),
    created_at=datetime(2026, 1, 5, tzinfo=UTC),
)


def _env(argv: list[str]) -> dict[str, str]:
    pairs = (flag.partition("=") for flag in docker_env_flags(argv))
    return {key: value for key, sep, value in pairs if sep}


async def test_a_rework_run_starts_from_the_card_branch_head() -> None:
    harness = Harness(_changes(FileChange(FEATURE, "M", 1, 0)))
    card = CardState(branch=CARD_BRANCH, base_sha=CARD_BASE)
    result = await harness.run(card=card, review=BLOCKERS)
    assert result.is_success, result.outcome
    assert harness.host.branch_bundles == [(CARD_BRANCH, CARD_BASE)]
    assert harness.host.snapshots == 0
    env = _env(harness.processes.argv)
    assert env["AGENT_BASE_SHA"] == CARD_BASE
    assert "AGENT_MODE" not in env
    # A new branch from that head, checked and reported against the card's base.
    _, _, branch, base = harness.host.published[0]
    assert (branch, base) == (BRANCH, CARD_BASE)
    assert harness.host.changes_asked == [(CARD_BASE, BRANCH)]
    assert (result.branch, result.base_sha) == (BRANCH, CARD_BASE)


async def test_a_first_dev_run_snapshots_the_working_tree() -> None:
    harness = Harness(_changes())
    result = await harness.run()
    assert result.is_success
    assert harness.host.snapshots == 1
    assert harness.host.branch_bundles == []
    assert "AGENT_BASE_SHA" not in _env(harness.processes.argv)


async def test_a_review_without_a_card_branch_snapshots_as_usual() -> None:
    harness = Harness(_changes())
    await harness.run(review=BLOCKERS)
    assert harness.host.snapshots == 1
    assert harness.host.branch_bundles == []


def test_dev_runs_are_still_labelled_writer() -> None:
    harness = Harness(_changes())
    assert harness.runner.labels[RunKind.DEV] == "writer"
