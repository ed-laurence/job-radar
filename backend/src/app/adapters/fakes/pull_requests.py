"""In-memory `PullRequests` (ARCH-04): PRs by head branch, plus an optional outage."""

from __future__ import annotations

from app.domain.errors import UpstreamUnavailable
from app.domain.ports.devboard import PullRequest


class FakePullRequests:
    def __init__(self) -> None:
        self.by_branch: dict[str, PullRequest] = {}
        self.calls: list[str] = []
        # Set to make every `find` raise, like gh being missing or signed out.
        self.outage: UpstreamUnavailable | None = None

    def put(self, branch: str, pull_request: PullRequest) -> None:
        self.by_branch[branch] = pull_request

    async def find(self, branch: str) -> PullRequest | None:
        self.calls.append(branch)
        if self.outage is not None:
            raise self.outage
        return self.by_branch.get(branch)
