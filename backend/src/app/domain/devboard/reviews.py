"""Human review reads: the latest review, the branch diff and the patch (spec `getTaskReview`,
`getTaskDiff`, `getTaskPatch`).

Review text and diffs are shown to people, so they pass the redaction filter on the way out
(SEC-06). A diff is cut at `MAX_DIFF_CHARS` after redaction; a patch is never cut, because a
cut patch can't be applied with `git am`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

from app.domain.devboard.model import Actor, ReviewFinding, ReviewRecord, TaskRecord
from app.domain.errors import NotFound
from app.domain.ports.devboard import BranchDiff, BranchViews, TaskStore, TextRedactor

MAX_DIFF_CHARS = 400_000
NO_BRANCH = "This card has no agent branch yet."


@dataclass(frozen=True)
class DiffView:
    diff: BranchDiff
    truncated: bool


class ReviewService:
    def __init__(self, tasks: TaskStore, branches: BranchViews, redactor: TextRedactor) -> None:
        self._tasks = tasks
        self._branches = branches
        self._redactor = redactor

    async def latest(self, actor: Actor, task_id: uuid.UUID) -> ReviewRecord:
        await self._task(actor, task_id)
        review = await self._tasks.latest_review(actor.user_id, task_id)
        if review is None:
            raise NotFound("This card hasn't been reviewed yet.")
        findings = tuple([await self._finding(finding) for finding in review.findings])
        return replace(review, summary=await self._redact(review.summary), findings=findings)

    async def diff(self, actor: Actor, task_id: uuid.UUID) -> DiffView:
        branch, base_sha = await self._branch(actor, task_id)
        found = await self._branches.diff(base_sha, branch)
        # Cut before redacting too, so a huge diff isn't scanned in full.
        clean = await self._redact(found.text[: MAX_DIFF_CHARS + 1])
        truncated = len(found.text) > MAX_DIFF_CHARS or len(clean) > MAX_DIFF_CHARS
        return DiffView(replace(found, text=clean[:MAX_DIFF_CHARS]), truncated=truncated)

    async def patch(self, actor: Actor, task_id: uuid.UUID) -> str:
        branch, base_sha = await self._branch(actor, task_id)
        return await self._redact(await self._branches.patch(base_sha, branch))

    async def _task(self, actor: Actor, task_id: uuid.UUID) -> TaskRecord:
        task = await self._tasks.get(actor.user_id, task_id)
        if task is None:
            raise NotFound("Task not found.")
        return task

    async def _branch(self, actor: Actor, task_id: uuid.UUID) -> tuple[str, str]:
        task = await self._task(actor, task_id)
        if not task.branch or not task.base_sha:
            raise NotFound(NO_BRANCH)
        return task.branch, task.base_sha

    async def _finding(self, finding: ReviewFinding) -> ReviewFinding:
        return replace(
            finding,
            evidence=await self._redact(finding.evidence),
            repro=await self._redact_or_none(finding.repro),
            fix=await self._redact_or_none(finding.fix),
        )

    async def _redact(self, text: str) -> str:
        return await self._redactor.redact(text) if text else text

    async def _redact_or_none(self, text: str | None) -> str | None:
        return None if text is None else await self._redact(text)
