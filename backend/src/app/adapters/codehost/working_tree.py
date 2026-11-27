"""`RepoFiles` adapters: the repo's file list that agents' paths are checked against.

- `WorkingTreeFiles` lists the project's working tree with git, `.gitignore`d files left out.
  Like `LocalGitHost.snapshot` it never names the project's own `.git`: git reads the tree
  through a throwaway, empty `GIT_DIR` in a temp dir (with an empty index every file is
  "untracked", so `ls-files --others --exclude-standard` is the non-ignored tree). No system
  or global git config, optional locks off; errors carry the git verb and exit code only.
- `ListedRepoFiles` reads a frozen newline listing (offline replay: a stable prompt hash).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from app.adapters.agents.process import capture
from app.domain.errors import UpstreamUnavailable

GIT_TIMEOUT_S = 60.0
_PASSTHROUGH_ENV = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "COMSPEC", "PATHEXT", "LANG")


def _git_env() -> dict[str, str]:
    env = {name: os.environ[name] for name in _PASSTHROUGH_ENV if name in os.environ}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_OPTIONAL_LOCKS="0",
    )
    return env


def parse_listing(text: str) -> frozenset[str]:
    """Repo-relative `/` paths, one per entry; blanks and directory entries dropped."""
    entries = (line.strip().replace("\\", "/") for line in text.replace("\0", "\n").splitlines())
    return frozenset(entry for entry in entries if entry and not entry.endswith("/"))


class WorkingTreeFiles:
    def __init__(self, repo: Path, *, git: str = "git") -> None:
        self._repo = repo.resolve()
        self._exe = git

    async def _git(self, args: list[str], verb: str, cwd: Path) -> str:
        argv = [self._exe, "-c", "core.longpaths=true", "-c", "core.hooksPath=", *args]
        try:
            done = await capture(argv, env=_git_env(), cwd=cwd, timeout_s=GIT_TIMEOUT_S)
        except TimeoutError:
            raise UpstreamUnavailable(f"git {verb} timed out") from None
        except OSError:
            raise UpstreamUnavailable("git is not installed") from None
        if done.code != 0:
            raise UpstreamUnavailable(f"git {verb} failed (exit {done.code})")
        return done.stdout.decode("utf-8", "replace")

    async def paths(self) -> frozenset[str]:
        scratch = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="jr-files-"))
        try:
            git_dir = scratch / "files.git"
            await self._git(["init", "-q", "--bare", str(git_dir)], "init", scratch)
            tree = ["--git-dir", str(git_dir), "--work-tree", str(self._repo)]
            listing = ["-c", "core.bare=false", "ls-files", "-z", "--others", "--exclude-standard"]
            out = await self._git([*tree, *listing], "ls-files", scratch)
        finally:
            await asyncio.to_thread(shutil.rmtree, scratch, ignore_errors=True)
        return parse_listing(out)


class ListedRepoFiles:
    def __init__(self, listing: Path) -> None:
        self._listing = listing

    async def paths(self) -> frozenset[str]:
        try:
            text = await asyncio.to_thread(self._listing.read_text, encoding="utf-8")
        except OSError:
            raise UpstreamUnavailable("The repo file listing could not be read.") from None
        return parse_listing(text)
