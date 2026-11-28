"""GitHubHost: the code host for `CODE_HOST=github` (docs/features/github-prs.md).

It is LocalGitHost (branches are published, checked and diffed in the scratch remote) with two
differences:
- `snapshot` fetches GitHub `main` with the deploy key into the scratch remote
  (`refs/remotes/github/main`) and bundles that commit. It never reads the project's working
  tree, so uncommitted local changes never reach an agent PR.
- `push` adds the publish commit on top of a checked scratch branch (`git commit-tree`, unchanged
  tree, fixed publisher identity, the trailers agent-pr.yml reads), moves the scratch branch to
  it (so a rework run starts from it and its push fast-forwards), then pushes it to
  `agent/task-<uuid>` on GitHub. Never forced: a non-fast-forward push fails.

Every GitHub call uses GIT_SSH_COMMAND with the deploy key, IdentitiesOnly, the pinned
known_hosts file and StrictHostKeyChecking=yes, plus LocalGitHost's no-global-config env. Only
paths reach the env; the key's content is never read here. Errors carry the verb and exit code.
"""

from __future__ import annotations

import asyncio
import re
import shlex
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from app.adapters.codehost.local_git import (
    GitError,
    LocalGitHost,
    Snapshot,
    check_agent_ref,
)
from app.domain.ports.devboard import github_branch

GITHUB_TRACKING_REF = "refs/remotes/github/main"
PUBLISHER_NAME = "jobradar-publisher"
PUBLISHER_EMAIL = "publisher@example.test"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
_MISSING_ACCESS = "The GitHub deploy key or known_hosts file is missing."
# agent-pr.yml accepts at most five digits per count.
_MAX_COUNT = 99_999


def github_url(repo: str) -> str:
    """The SSH URL of `owner/name` (validated as settings do)."""
    if not _REPO_RE.match(repo) or any(part in {".", ".."} for part in repo.split("/")):
        raise ValueError("GITHUB_REPO must be owner/name")
    return f"git@github.com:{repo}.git"


@dataclass(frozen=True)
class GitHubAccess:
    """Where GitHub is and how to reach it. Tests point `url` at a local bare repo."""

    url: str
    deploy_key: Path
    known_hosts: Path

    def __repr__(self) -> str:
        return "GitHubAccess(...)"


@dataclass(frozen=True)
class PublishTrailers:
    """The publish commit's structured fields: the sandbox's own test re-run counts and the
    changed lines PUB-SIZE counts (regenerated code excluded)."""

    task_id: uuid.UUID
    passed: int
    failed: int
    diff_lines: int


@dataclass(frozen=True)
class PushedBranch:
    branch: str
    sha: str


def ssh_command(access: GitHubAccess) -> str:
    """GIT_SSH_COMMAND for every GitHub call: the deploy key only, the pinned host key only,
    no user ssh config, never a prompt."""
    key = shlex.quote(access.deploy_key.as_posix())
    known = shlex.quote(access.known_hosts.as_posix())
    return (
        f"ssh -F none -i {key} -o IdentitiesOnly=yes -o UserKnownHostsFile={known}"
        " -o StrictHostKeyChecking=yes -o BatchMode=yes"
    )


def _count(value: int) -> int:
    return max(0, min(_MAX_COUNT, value))


def publish_message(trailers: PublishTrailers) -> str:
    """`jobradar: publish task-<uuid>` and the trailers agent-pr.yml reads (structured only)."""
    task = f"task-{trailers.task_id}"
    passed, failed = _count(trailers.passed), _count(trailers.failed)
    total = _count(passed + failed)
    return (
        f"jobradar: publish {task}\n"
        "\n"
        f"Jobradar-Task: {task}\n"
        "Jobradar-Verdict: pass\n"
        f"Jobradar-Tests: {passed}/{total}\n"
        f"Jobradar-Diff-Lines: {_count(trailers.diff_lines)}\n"
    )


class GitHubHost(LocalGitHost):
    def __init__(self, repo: Path, remote: Path, access: GitHubAccess, *, git: str = "git") -> None:
        # `repo` only keeps the scratch remote outside the project; it is never read.
        super().__init__(repo, remote, git=git)
        self._access = access

    def __repr__(self) -> str:
        return "GitHubHost(...)"

    async def _require_access(self) -> None:
        access = self._access
        present = await asyncio.to_thread(
            lambda: access.deploy_key.is_file() and access.known_hosts.is_file()
        )
        if not present:
            raise GitError(_MISSING_ACCESS)

    def _github_env(self) -> Mapping[str, str]:
        return {"GIT_SSH_COMMAND": ssh_command(self._access)}

    # ------------------------------------------------------------ snapshot
    async def snapshot(self, dest: Path) -> Snapshot:
        """Fetch GitHub `main` into the scratch remote and bundle it (HEAD + main)."""
        await self._require_access()
        await self._ensure_remote()
        refspec = f"+refs/heads/main:{GITHUB_TRACKING_REF}"
        fetch = ["fetch", "-q", "--no-tags", "--", self._access.url, refspec]
        await self._remote_git_env(fetch, self._github_env())
        sha = (await self._remote_git("rev-parse", "--verify", GITHUB_TRACKING_REF)).strip()
        if not _SHA_RE.match(sha):
            raise GitError("git rev-parse failed (exit 0)")
        return await self._bundle_ref(GITHUB_TRACKING_REF, sha, dest)

    # ------------------------------------------------------------ publish commit + push
    async def push(self, branch: str, trailers: PublishTrailers) -> PushedBranch:
        """Add the publish commit on `branch` (a checked scratch branch) and push it to the
        card's GitHub branch. A non-fast-forward or any failure raises `GitError`."""
        check_agent_ref(branch, "branch")
        target = github_branch(trailers.task_id)
        check_agent_ref(target, "target")
        await self._require_access()
        head = (await self._remote_git("rev-parse", "--verify", f"refs/heads/{branch}")).strip()
        identity = {
            "GIT_AUTHOR_NAME": PUBLISHER_NAME,
            "GIT_AUTHOR_EMAIL": PUBLISHER_EMAIL,
            "GIT_COMMITTER_NAME": PUBLISHER_NAME,
            "GIT_COMMITTER_EMAIL": PUBLISHER_EMAIL,
        }
        subject, _, body = publish_message(trailers).partition("\n\n")
        commit = ["commit-tree", f"{head}^{{tree}}", "-p", head, "-m", subject, "-m", body]
        sha = (await self._remote_git_env(commit, identity)).strip()
        if not _SHA_RE.match(sha):
            raise GitError("git commit-tree failed (exit 0)")
        await self._remote_git("update-ref", f"refs/heads/{branch}", sha, head)
        refspec = f"{sha}:refs/heads/{target}"
        push = ["push", "-q", "--no-tags", "--", self._access.url, refspec]
        await self._remote_git_env(push, self._github_env())
        return PushedBranch(branch=target, sha=sha)
