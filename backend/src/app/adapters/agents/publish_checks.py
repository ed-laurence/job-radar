"""The Publisher's checks on a freshly published dev-run branch (docs/features/agent-review.md §1).

The domain rules (`app.domain.devboard.publisher`) plus `PUB-SECRET`, which needs the redaction
filter: every added line goes through its fast rules (secret patterns, then the denylist), and a
line the filter changes is a violation. The message counts lines and never quotes one.
"""

from __future__ import annotations

from collections import Counter
from typing import Protocol

from app.domain.devboard.model import PlanRecord
from app.domain.devboard.paths import is_regenerated
from app.domain.devboard.publisher import violations
from app.domain.errors import DomainError
from app.domain.ports.devboard import ChangeSet, RunContext, TextRedactor

MAX_SHOWN = 10


class SecretFilter(Protocol):
    """`app.guards.redaction.Redactor`'s fast path: secret patterns and the denylist only."""

    def redact_fast(self, text: str, *, is_complete: bool = False) -> str: ...


def run_leases(plan: PlanRecord | None) -> tuple[str, ...]:
    """Every subtask's lease paths: the dev run may change any of them."""
    if plan is None:
        return ()
    return tuple(dict.fromkeys(path for s in plan.subtasks for path in s.lease_paths))


async def _is_secret(
    line: str, secrets: SecretFilter | None, redactor: TextRedactor | None
) -> bool:
    if secrets is not None:
        return secrets.redact_fast(line, is_complete=True) != line
    if redactor is None:
        return False
    try:
        return await redactor.redact(line) != line
    except DomainError:
        return True  # fail closed: a line that can't be scanned isn't published


async def secret_violations(
    changes: ChangeSet, secrets: SecretFilter | None, redactor: TextRedactor | None
) -> tuple[str, ...]:
    hits: Counter[str] = Counter()
    for path, line in changes.added_lines:
        if await _is_secret(line, secrets, redactor):
            hits[path] += 1
    return tuple(
        f"PUB-SECRET: {path} — {count} added line{'' if count == 1 else 's'}"
        f" match{'es' if count == 1 else ''} the secret rules"
        for path, count in hits.items()
    )


async def branch_violations(
    context: RunContext,
    changes: ChangeSet,
    secrets: SecretFilter | None,
    redactor: TextRedactor | None,
) -> tuple[str, ...]:
    rules = violations(changes, leases=run_leases(context.plan), labels=context.task.labels)
    return (*rules, *await secret_violations(changes, secrets, redactor))


def changed_lines(changes: ChangeSet) -> int:
    """Added + deleted lines, regenerated code excluded: what PUB-SIZE counts (a rename is
    regenerated if either path is). The publish commit's `Jobradar-Diff-Lines`."""
    return sum(
        change.added + change.deleted
        for change in changes.files
        if not any(is_regenerated(path) for path in (change.path, change.old_path) if path)
    )


def refusal(found: tuple[str, ...]) -> str:
    """The run's outcome: the first `MAX_SHOWN` violations and how many more there were."""
    shown = "; ".join(found[:MAX_SHOWN])
    more = len(found) - MAX_SHOWN
    tail = f"; and {more} more" if more > 0 else ""
    return f"The Publisher refused the branch: {shown}{tail}."
