"""CODE_HOST=github in the runner: after the Publisher's checks pass, a done dev run gets the
publish commit and is pushed to `agent/task-<id>` (docs/features/github-prs.md steps 3-4).
Local mode (no pusher) pushes nothing. A failed push fails the run with a safe message and keeps
the scratch branch. The message and trailer format satisfy agent-pr.yml's checks."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

from app.adapters.agents.docker_runner import (
    DockerAgentRunner,
    SandboxConfig,
    SandboxDeps,
    SandboxResult,
)
from app.adapters.agents.publish_checks import changed_lines
from app.adapters.codehost.github import (
    GitHubAccess,
    PublishTrailers,
    github_url,
    publish_message,
    ssh_command,
)
from app.domain.devboard.model import Subtask
from app.domain.devboard.publisher import parse_dev_run_outcome
from app.domain.ports.devboard import ChangeSet, FileChange, github_branch
from tests.unit.agents_runner.fakes import (
    BASE_SHA,
    PROXY_SECRET,
    TASK_ID,
    FakeCodeHost,
    FakeCoordinatorTokens,
    FakeProcessRunner,
    FakePusher,
    FakeRedactor,
    FakeTools,
    FakeUsageProbe,
    context,
    handoff,
    plan,
    writes,
)

WALL = 1_800_000_000.0
BRANCH = "agent/11111111-aaaaaaaa"
FEATURE = "web/src/features/board/Card.tsx"
LEASED = plan(subtasks=(Subtask("s1", "Badge", "frontend-engineer", ("web/src/features/board/",)),))
PUSHED = f"Pushed agent/task-{TASK_ID}; the PR opens from agent-pr.yml."
CLEAN = ChangeSet(files=(FileChange(FEATURE, "M", 3, 1),), added_lines=((FEATURE, "const a = 1;"),))


class Harness:
    def __init__(self, change_set: ChangeSet = CLEAN, *, pusher: FakePusher | None = None) -> None:
        self.host = FakeCodeHost(change_set=change_set)
        self.processes = FakeProcessRunner()
        self.pusher = pusher
        deps = SandboxDeps(
            host=self.host,
            processes=self.processes,
            coordinator_tokens=FakeCoordinatorTokens(),
            usage=FakeUsageProbe(),
            redactor=FakeRedactor(),
            pusher=pusher,
        )
        config = SandboxConfig(image="jobradar-sandbox-writer:dev", poll_s=0.01)
        self.runner = DockerAgentRunner(config, deps, PROXY_SECRET, wall=lambda: WALL)

    async def run(self) -> SandboxResult:
        result = await self.runner.run(context(plan_record=LEASED), FakeTools())
        assert isinstance(result, SandboxResult)
        return result


async def test_github_mode_pushes_a_clean_done_run_with_its_counts() -> None:
    pusher = FakePusher()
    harness = Harness(pusher=pusher)
    result = await harness.run()
    assert result.is_success, result.outcome
    assert (result.branch, result.base_sha) == (BRANCH, BASE_SHA)
    assert pusher.pushed == [
        (BRANCH, PublishTrailers(task_id=TASK_ID, passed=5, failed=0, diff_lines=4))
    ]
    assert PUSHED in result.outcome
    # The reviewer still reads the diff stat and test counts back.
    parsed = parse_dev_run_outcome(result.outcome)
    assert parsed is not None
    assert (parsed.branch, parsed.passed, parsed.failed) == (BRANCH, 5, 0)
    assert (parsed.files_changed, parsed.insertions, parsed.deletions) == (1, 10, 2)


async def test_local_mode_pushes_nothing() -> None:
    result = await Harness().run()
    assert result.is_success
    assert "Pushed" not in result.outcome
    assert parse_dev_run_outcome(result.outcome) is not None


async def test_a_refused_branch_is_never_pushed() -> None:
    outside = ChangeSet(files=(FileChange("docs/other/x.md", "A", 1, 0),), added_lines=())
    pusher = FakePusher()
    result = await Harness(outside, pusher=pusher).run()
    assert not result.is_success
    assert pusher.pushed == []


async def test_a_failed_handoff_is_never_pushed() -> None:
    pusher = FakePusher()
    harness = Harness(pusher=pusher)
    harness.processes.write = writes(handoff(status="failed"))
    result = await harness.run()
    assert not result.is_success
    assert pusher.pushed == []
    assert "Pushed" not in result.outcome


async def test_a_failed_push_fails_the_run_and_keeps_the_scratch_branch() -> None:
    harness = Harness(pusher=FakePusher(fail=True))
    result = await harness.run()
    assert not result.is_success
    assert result.outcome.startswith(
        f"Could not push agent/task-{TASK_ID} to GitHub (git push failed (exit 1))."
    )
    assert result.branch == BRANCH
    assert harness.host.deleted == []
    for secret in (PROXY_SECRET.decode(), FakeCoordinatorTokens().token):
        assert secret not in result.outcome


# ------------------------------------------------------------------ message and trailers
def _trailer(message: str, key: str) -> str:
    """agent-pr.yml's read: the last paragraph's `key: value` line."""
    last = message.rstrip("\n").split("\n\n")[-1]
    values = [line.split(": ", 1)[1] for line in last.splitlines() if line.startswith(f"{key}: ")]
    return values[0]


def test_the_publish_message_satisfies_agent_pr_yml() -> None:
    task = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")
    message = publish_message(PublishTrailers(task_id=task, passed=12, failed=2, diff_lines=88))
    assert message.splitlines()[0] == f"jobradar: publish task-{task}"
    value = _trailer(message, "Jobradar-Task")
    assert re.fullmatch(r"task-[0-9a-f-]{8,36}", value)
    assert github_branch(task) == f"agent/{value}"
    assert _trailer(message, "Jobradar-Verdict") == "pass"
    assert _trailer(message, "Jobradar-Tests") == "12/14"
    assert _trailer(message, "Jobradar-Diff-Lines") == "88"


def test_trailer_counts_stay_within_the_workflow_patterns() -> None:
    message = publish_message(
        PublishTrailers(task_id=TASK_ID, passed=10**7, failed=-3, diff_lines=10**6)
    )
    assert re.fullmatch(r"[0-9]{1,5}/[0-9]{1,5}", _trailer(message, "Jobradar-Tests"))
    assert re.fullmatch(r"[0-9]{1,5}", _trailer(message, "Jobradar-Diff-Lines"))


def test_changed_lines_match_pub_size_and_skip_regenerated_code() -> None:
    changes = ChangeSet(
        files=(
            FileChange(FEATURE, "M", 3, 1),
            FileChange("backend/src/app/generated/models.py", "M", 500, 20),
            FileChange("web/src/old.ts", "R", 2, 2, old_path="web/src/lib/api/generated/x.ts"),
            FileChange("api/openapi.yaml", "M", 5, 0),
        ),
        added_lines=(),
    )
    assert changed_lines(changes) == 9


def test_the_ssh_command_pins_the_key_and_host() -> None:
    access = GitHubAccess(
        url=github_url("owner-x/repo.name"),
        deploy_key=Path("/keys/my key"),
        known_hosts=Path("/keys/known_hosts"),
    )
    command = ssh_command(access)
    assert command.startswith("ssh ")
    for part in (
        "-i '/keys/my key'",
        "-o IdentitiesOnly=yes",
        "-o UserKnownHostsFile=/keys/known_hosts",
        "-o StrictHostKeyChecking=yes",
    ):
        assert part in command
    assert access.url == "git@github.com:owner-x/repo.name.git"
    assert "keys" not in repr(access)


@pytest.mark.parametrize("repo", ["owner", "a/b/c", "../x", "a/..", "a b/c", "-x/y;rm"])
def test_github_url_refuses_anything_but_owner_name(repo: str) -> None:
    with pytest.raises(ValueError, match="owner/name"):
        github_url(repo)
