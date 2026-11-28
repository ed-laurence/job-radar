"""LocalGitHost: the code host for local agent runs before the repo is on GitHub (plan.md
"Repository, commits and the GCP bootstrap"). Branches and diffs live in a scratch bare remote
outside the project (`AGENT_REMOTE_PATH`).

No git command here names the project's own `.git`:
- `snapshot` reads the project's *working tree* through a throwaway `GIT_DIR` and index in a
  temp dir (so it also works before the first commit, and `.gitignore`d files stay out);
- `publish`, `diff`, `diff_stat`, `changes`, `delete_branch` and `bundle_branch` run with
  `--git-dir=<scratch remote>` (or a throwaway repo fetching from it) only.
`LocalBranchViews` serves the human-review diff and patch (the `BranchViews` port) from the same
scratch remote.
Every call runs with no system or global git config (no hooks, helpers or signing) and with
optional locks off. Error messages carry the git verb and exit code, never git's output.
`GitHubHost` (`github.py`, CODE_HOST=github) builds on this class.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.adapters.agents.process import capture
from app.adapters.codehost.git_changes import ChangeParseError, change_set
from app.domain.errors import NotFound
from app.domain.ports.devboard import BranchDiff, ChangeSet

GIT_TIMEOUT_S = 300.0
SHORT_ID = 8
_SHA_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
_AGENT_REF_RE = re.compile(r"^agent/[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_PASSTHROUGH_ENV = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "COMSPEC", "PATHEXT", "LANG")
_IDENTITY = "jobradar-snapshot"
_IDENTITY_EMAIL = "snapshot@example.test"
# Options whose value follows them: never the verb named in an error.
_VALUE_OPTIONS = frozenset({"--git-dir", "--work-tree", "-c"})
_DIFF_FLAGS = ("--no-color", "--no-ext-diff", "--no-textconv", "-M")


class GitError(Exception):
    """A git step failed. The message is safe to show (verb and exit code only)."""


@dataclass(frozen=True)
class Snapshot:
    bundle: Path
    base_sha: str


@dataclass(frozen=True)
class DiffStat:
    files_changed: int
    insertions: int
    deletions: int
    text: str


@dataclass(frozen=True)
class PublishedBranch:
    branch: str
    base_sha: str
    head_sha: str
    stat: DiffStat


class CodeHost(Protocol):
    """Snapshot the base, publish an agent's result bundle as a branch, and diff it.
    `GitHubHost` (CODE_HOST=github) snapshots GitHub `main` instead of the working tree."""

    async def snapshot(self, dest: Path) -> Snapshot: ...

    async def publish(
        self, bundle: Path, source_ref: str, branch: str, base_sha: str
    ) -> PublishedBranch: ...

    async def diff(self, base_sha: str, branch: str) -> str: ...

    async def diff_stat(self, base_sha: str, branch: str) -> DiffStat: ...

    async def changes(self, base_sha: str, branch: str) -> ChangeSet: ...

    async def delete_branch(self, branch: str) -> None: ...

    async def bundle_branch(self, branch: str, base_sha: str, dest: Path) -> Snapshot: ...


def branch_name(task_id: uuid.UUID, run_id: uuid.UUID) -> str:
    return f"agent/{task_id.hex[:SHORT_ID]}-{run_id.hex[:SHORT_ID]}"


def check_agent_ref(ref: str, what: str) -> None:
    if not _AGENT_REF_RE.match(ref) or ".." in ref or ref.endswith((".", ".lock")):
        raise ValueError(f"{what} must be an agent/<name> branch")


def _check_sha(sha: str) -> None:
    if not _SHA_RE.match(sha):
        raise ValueError("base_sha must be a full commit id")


def _git_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = {name: os.environ[name] for name in _PASSTHROUGH_ENV if name in os.environ}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_OPTIONAL_LOCKS="0",
        GIT_AUTHOR_NAME=_IDENTITY,
        GIT_AUTHOR_EMAIL=_IDENTITY_EMAIL,
        GIT_COMMITTER_NAME=_IDENTITY,
        GIT_COMMITTER_EMAIL=_IDENTITY_EMAIL,
    )
    if extra:
        env.update(extra)
    return env


_CHMOD_BATCH = 100


def _scripts(repo: Path, paths: Sequence[str]) -> list[str]:
    """The paths (relative to `repo`) whose content starts with `#!`."""
    found: list[str] = []
    for path in paths:
        try:
            with (repo / path).open("rb") as handle:
                if handle.read(2) == b"#!":
                    found.append(path)
        except OSError:
            continue
    return found


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _verb(args: Sequence[str]) -> str:
    """The git subcommand: the first bare word that isn't an option's value."""
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg in _VALUE_OPTIONS:
            skip = True
        elif not arg.startswith("-") and "=" not in arg:
            return arg
    return "git"


class LocalGitHost:
    def __init__(self, repo: Path, remote: Path, *, git: str = "git") -> None:
        self._repo = repo.resolve()
        self._remote = remote.expanduser().resolve()
        if _inside(self._remote, self._repo):
            raise ValueError("The agent remote must live outside the project folder.")
        self._exe = git

    async def _git(
        self, args: Sequence[str], *, cwd: Path, env: Mapping[str, str] | None = None
    ) -> str:
        code, out = await self._git_code(args, cwd=cwd, env=env)
        if code != 0:
            raise GitError(f"git {_verb(args)} failed (exit {code})")
        return out

    async def _git_code(
        self, args: Sequence[str], *, cwd: Path, env: Mapping[str, str] | None = None
    ) -> tuple[int, str]:
        """`env` adds to the fixed no-global-config env (e.g. GIT_SSH_COMMAND); never logged."""
        argv = [self._exe, "-c", "core.longpaths=true", "-c", "core.hooksPath=", *args]
        try:
            done = await capture(argv, env=_git_env(env), cwd=cwd, timeout_s=GIT_TIMEOUT_S)
        except TimeoutError:
            raise GitError(f"git {_verb(args)} timed out") from None
        except OSError:
            raise GitError("git is not installed") from None
        return done.code, done.stdout.decode("utf-8", "replace")

    async def _remote_git(self, *args: str) -> str:
        return await self._git(["--git-dir", str(self._remote), *args], cwd=self._remote)

    async def _remote_git_env(self, args: Sequence[str], env: Mapping[str, str]) -> str:
        return await self._git(["--git-dir", str(self._remote), *args], cwd=self._remote, env=env)

    async def remote_git(self, *args: str) -> str:
        """A read-only git call on the scratch remote (callers validate refs and shas)."""
        return await self._remote_git(*args)

    # ------------------------------------------------------------ snapshot
    async def snapshot(self, dest: Path) -> Snapshot:
        """Bundle the project's working tree (HEAD + main, one commit) into `dest`."""
        scratch = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="jr-snapshot-"))
        try:
            await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)
            git_dir = scratch / "snapshot.git"
            await self._git(["init", "-q", "--bare", "-b", "main", str(git_dir)], cwd=scratch)
            tree = ["--git-dir", str(git_dir), "--work-tree", str(self._repo)]
            await self._git([*tree, "-c", "core.bare=false", "add", "-A"], cwd=scratch)
            await self._mark_scripts_executable(tree, scratch)
            message = "Snapshot of the working tree for an agent run"
            commit = ["commit", "-q", "--no-verify", "--allow-empty", "-m", message]
            await self._git([*tree, "-c", "core.bare=false", *commit], cwd=scratch)
            sha = await self._git(["--git-dir", str(git_dir), "rev-parse", "HEAD"], cwd=scratch)
            bundle = ["bundle", "create", "-q", str(dest), "HEAD", "main"]
            await self._git(["--git-dir", str(git_dir), *bundle], cwd=scratch)
        finally:
            await asyncio.to_thread(shutil.rmtree, scratch, ignore_errors=True)
        return Snapshot(bundle=dest, base_sha=sha.strip())

    async def _mark_scripts_executable(self, tree: list[str], scratch: Path) -> None:
        """Git on Windows records no exec bit, so scripts would reach the sandbox as 100644
        ("Permission denied" on `scripts/gen-api.sh`). Files whose first line is a `#!` are
        made executable in the snapshot; the project's own `.git` is never read."""
        listed = await self._git([*tree, "-c", "core.bare=false", "ls-files", "-z"], cwd=scratch)
        paths = [path for path in listed.split("\0") if path]
        scripts = await asyncio.to_thread(_scripts, self._repo, paths)
        for start in range(0, len(scripts), _CHMOD_BATCH):
            batch = scripts[start : start + _CHMOD_BATCH]
            chmod = ["update-index", "--chmod=+x", "--", *batch]
            await self._git([*tree, "-c", "core.bare=false", *chmod], cwd=scratch)

    # ------------------------------------------------------------ publish
    async def _ensure_remote(self) -> None:
        if await asyncio.to_thread((self._remote / "HEAD").is_file):
            return
        await asyncio.to_thread(self._remote.mkdir, parents=True, exist_ok=True)
        await self._git(["init", "-q", "--bare", str(self._remote)], cwd=self._remote)

    async def publish(
        self, bundle: Path, source_ref: str, branch: str, base_sha: str
    ) -> PublishedBranch:
        """Fetch `source_ref` from the result bundle into the remote as `branch` (never
        overwriting an existing branch), then check it descends from the snapshot."""
        check_agent_ref(source_ref, "source_ref")
        check_agent_ref(branch, "branch")
        _check_sha(base_sha)
        await self._ensure_remote()
        refspec = f"refs/heads/{source_ref}:refs/heads/{branch}"
        await self._remote_git("fetch", "-q", "--no-tags", str(bundle), refspec)
        head = (await self._remote_git("rev-parse", "--verify", f"refs/heads/{branch}")).strip()
        await self._remote_git("merge-base", "--is-ancestor", base_sha, head)
        return PublishedBranch(
            branch=branch,
            base_sha=base_sha,
            head_sha=head,
            stat=await self.diff_stat(base_sha, branch),
        )

    # ------------------------------------------------------------ diffs
    async def diff(self, base_sha: str, branch: str) -> str:
        _check_sha(base_sha)
        check_agent_ref(branch, "branch")
        return await self._remote_git("diff", "--no-color", "--no-ext-diff", base_sha, branch)

    async def diff_stat(self, base_sha: str, branch: str) -> DiffStat:
        _check_sha(base_sha)
        check_agent_ref(branch, "branch")
        numstat = await self._remote_git("diff", "--numstat", base_sha, branch)
        text = await self._remote_git("diff", "--stat", "--no-color", base_sha, branch)
        files = insertions = deletions = 0
        for line in numstat.splitlines():
            added, removed, *_ = [*line.split("\t"), "", ""]
            if not added:
                continue
            files += 1
            insertions += int(added) if added.isdigit() else 0
            deletions += int(removed) if removed.isdigit() else 0
        return DiffStat(files_changed=files, insertions=insertions, deletions=deletions, text=text)

    # ------------------------------------------------------------ the Publisher's view
    async def changes(self, base_sha: str, branch: str) -> ChangeSet:
        """Per file the status and line counts, plus every added line, for base..branch."""
        _check_sha(base_sha)
        check_agent_ref(branch, "branch")
        span = (base_sha, f"refs/heads/{branch}")
        status = await self._remote_git("diff", "--name-status", "-z", *_DIFF_FLAGS, *span)
        counts = await self._remote_git("diff", "--numstat", "-z", *_DIFF_FLAGS, *span)
        patch = await self._remote_git("diff", "-U0", *_DIFF_FLAGS, *span)
        try:
            return change_set(status, counts, patch)
        except ChangeParseError:
            raise GitError("git diff output was not understood") from None

    async def has_branch(self, branch: str) -> bool:
        check_agent_ref(branch, "branch")
        if not await asyncio.to_thread((self._remote / "HEAD").is_file):
            return False
        args = ["--git-dir", str(self._remote), "rev-parse", "--verify", "--quiet"]
        code, _ = await self._git_code([*args, f"refs/heads/{branch}"], cwd=self._remote)
        return code == 0

    async def delete_branch(self, branch: str) -> None:
        """Remove a published branch (the Publisher refused it). Missing is fine."""
        if await self.has_branch(branch):
            await self._remote_git("update-ref", "-d", f"refs/heads/{branch}")

    async def bundle_branch(self, branch: str, base_sha: str, dest: Path) -> Snapshot:
        """Bundle a published branch's head (HEAD + main, with its history) as the input of a
        review or rework run. `base_sha` is the card's base; the branch must descend from it."""
        check_agent_ref(branch, "branch")
        _check_sha(base_sha)
        return await self._bundle_ref(f"refs/heads/{branch}", base_sha, dest)

    async def _bundle_ref(self, ref: str, base_sha: str, dest: Path) -> Snapshot:
        """Bundle the scratch remote's `ref` as `main` (HEAD + main, with its history); `ref`
        must descend from `base_sha`."""
        scratch = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="jr-branch-"))
        try:
            await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)
            git_dir = ["--git-dir", str(scratch / "branch.git")]
            await self._git(["init", "-q", "--bare", "-b", "main", git_dir[1]], cwd=scratch)
            refspec = f"{ref}:refs/heads/main"
            fetch = ["fetch", "-q", "--no-tags", str(self._remote), refspec]
            await self._git([*git_dir, *fetch], cwd=scratch)
            ancestry = ["merge-base", "--is-ancestor", base_sha, "refs/heads/main"]
            await self._git([*git_dir, *ancestry], cwd=scratch)
            bundle = ["bundle", "create", "-q", str(dest), "HEAD", "main"]
            await self._git([*git_dir, *bundle], cwd=scratch)
        finally:
            await asyncio.to_thread(shutil.rmtree, scratch, ignore_errors=True)
        return Snapshot(bundle=dest, base_sha=base_sha)


class LocalBranchViews:
    """`BranchViews` over LocalGitHost's scratch remote: the human-review diff and patch."""

    def __init__(self, host: LocalGitHost) -> None:
        self._host = host

    async def _require(self, base_sha: str, branch: str) -> None:
        _check_sha(base_sha)
        if not await self._host.has_branch(branch):
            raise NotFound("The agent branch no longer exists.")

    async def diff(self, base_sha: str, branch: str) -> BranchDiff:
        await self._require(base_sha, branch)
        stat = await self._host.diff_stat(base_sha, branch)
        text = await self._host.diff(base_sha, branch)
        return BranchDiff(
            branch=branch,
            base_sha=base_sha,
            files_changed=stat.files_changed,
            insertions=stat.insertions,
            deletions=stat.deletions,
            text=text,
        )

    async def patch(self, base_sha: str, branch: str) -> str:
        """`git format-patch` output for base..branch, for `git am` in a human's checkout."""
        await self._require(base_sha, branch)
        span = f"{base_sha}..refs/heads/{branch}"
        return await self._host.remote_git("format-patch", "--stdout", "--no-signature", span)
