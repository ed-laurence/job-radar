"""Fakes for agent review (ARCH-04): published branches (`BranchViews`) and the rubric ids
(`RubricIds`)."""

from __future__ import annotations

from collections.abc import Iterable

from app.domain.errors import NotFound
from app.domain.ports.devboard import BranchDiff


class FakeBranchViews:
    """Published branches by name (`BranchViews`); an unknown branch raises `NotFound`."""

    def __init__(self) -> None:
        self.branches: dict[str, tuple[BranchDiff, str]] = {}

    def publish(self, diff: BranchDiff, patch: str = "") -> None:
        self.branches[diff.branch] = (diff, patch)

    async def diff(self, base_sha: str, branch: str) -> BranchDiff:
        return self._find(base_sha, branch)[0]

    async def patch(self, base_sha: str, branch: str) -> str:
        return self._find(base_sha, branch)[1]

    def _find(self, base_sha: str, branch: str) -> tuple[BranchDiff, str]:
        found = self.branches.get(branch)
        if found is None or found[0].base_sha != base_sha:
            raise NotFound("Branch not found.")
        return found


class FixedRubricIds:
    """`RubricIds` with a fixed set (the real one reads docs/standards/review-rubric.yaml)."""

    def __init__(self, ids: Iterable[str]) -> None:
        self._ids = frozenset(ids)

    def ids(self) -> frozenset[str]:
        return self._ids
