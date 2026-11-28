"""`PullRequests` through the GitHub CLI, read-only, using the local gh login.

One `gh pr list --head <branch> --state all --limit 1` per lookup. gh runs with a minimal
environment (no tokens from ours: its own stored login is used), never prompts, and its output
is parsed strictly: anything unexpected, including a url outside the configured repo, is
`UpstreamUnavailable`. Error messages carry only the reason or gh's exit code, never output.
"""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Mapping, Sequence
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.adapters.agents.process import Completed, capture
from app.domain.errors import UpstreamUnavailable
from app.domain.ports.devboard import PullRequest, PullRequestState

GH_TIMEOUT_S = 30.0
# gh's exit code when it has no signed-in account.
GH_AUTH_EXIT = 4
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+")
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
# gh finds its login (config dir, keyring) and the network through these; nothing else passes.
_PASSTHROUGH_ENV = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "COMSPEC",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "XDG_CONFIG_HOME",
    "GH_CONFIG_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "SSL_CERT_FILE",
    "LANG",
)
_UNEXPECTED = "gh pr list gave unexpected output"
_STATES = {
    "OPEN": PullRequestState.OPEN,
    "CLOSED": PullRequestState.CLOSED,
    "MERGED": PullRequestState.MERGED,
}


class Capture(Protocol):
    def __call__(
        self, argv: Sequence[str], *, env: Mapping[str, str], cwd: Path | None, timeout_s: float
    ) -> Awaitable[Completed]: ...


class _ListedPr(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    url: str
    number: int = Field(gt=0)
    state: Literal["OPEN", "CLOSED", "MERGED"]
    merged_at: str | None = Field(alias="mergedAt")


# `--limit 1`: an empty list (no PR for the branch) or exactly one.
_LISTING = TypeAdapter(Annotated[list[_ListedPr], Field(max_length=1)])


def _gh_env() -> dict[str, str]:
    env = {name: os.environ[name] for name in _PASSTHROUGH_ENV if name in os.environ}
    env.update(GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1", NO_COLOR="1", GH_PAGER="")
    return env


class GhPullRequests:
    def __init__(self, repo: str, gh: str = "gh", *, run: Capture = capture) -> None:
        if not _REPO.fullmatch(repo):
            raise ValueError("GITHUB_REPO must be owner/name.")
        self._repo = repo
        self._exe = gh
        self._run = run
        self._url = re.compile(rf"https://github\.com/{re.escape(repo)}/pull/(\d+)", re.IGNORECASE)

    async def find(self, branch: str) -> PullRequest | None:
        if not _BRANCH.fullmatch(branch) or ".." in branch:
            raise ValueError("Not a branch name gh can be asked about.")
        argv = [
            self._exe,
            *("pr", "list", "--repo", self._repo, "--head", branch, "--state", "all"),
            *("--json", "url,number,state,mergedAt", "--limit", "1"),
        ]
        try:
            done = await self._run(argv, env=_gh_env(), cwd=None, timeout_s=GH_TIMEOUT_S)
        except TimeoutError:
            raise UpstreamUnavailable("gh pr list timed out") from None
        except OSError:
            raise UpstreamUnavailable("gh is not installed") from None
        if done.code == GH_AUTH_EXIT:
            raise UpstreamUnavailable("gh is not signed in")
        if done.code != 0:
            raise UpstreamUnavailable(f"gh pr list failed (exit {done.code})")
        return self._parse(done.stdout)

    def _parse(self, stdout: bytes) -> PullRequest | None:
        try:
            listed = _LISTING.validate_json(stdout)
        except ValidationError:
            raise UpstreamUnavailable(_UNEXPECTED) from None
        if not listed:
            return None
        item = listed[0]
        matched = self._url.fullmatch(item.url)
        if matched is None or int(matched[1]) != item.number:
            raise UpstreamUnavailable(_UNEXPECTED)
        return PullRequest(url=item.url, number=item.number, state=_STATES[item.state])
