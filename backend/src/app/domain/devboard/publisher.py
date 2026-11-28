"""The Publisher's checks on a published agent branch (docs/features/agent-review.md §1).

Pure rules over the branch's `ChangeSet`; any violation fails the run, and the runner deletes the
branch. Each message is "<RULE>: path — reason" and never quotes a changed line. `PUB-SECRET`
needs the redaction filter, so the runner adapter checks it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from app.domain.devboard.paths import (
    CONTRACT,
    contains,
    is_leasable,
    is_regenerated,
    is_test_file,
    is_test_infrastructure,
    is_writable,
)
from app.domain.devboard.plans import uncovered_tests
from app.domain.ports.devboard import ChangeSet, FileChange

MAX_CHANGED_LINES = 400
ALLOW_LABEL = "allow:"
PRAGMAS: tuple[str, ...] = (
    "pytest.mark.skip",
    "pytest.mark.xfail",
    "pytest.skip(",
    "type: ignore",
    "pyright: ignore",
    "noqa",
    "pragma: no cover",
    "eslint-disable",
    "biome-ignore",
    "@ts-expect-error",
    "@ts-ignore",
    ".skip(",
    ".only(",
)
# Statuses that touch a file that already existed on the base.
_EXISTING = frozenset({"M", "D", "R"})


def _paths(change: FileChange) -> tuple[str, ...]:
    """Every path a change touches: a rename also removes its old path."""
    if change.old_path is None or change.old_path == change.path:
        return (change.path,)
    return (change.old_path, change.path)


def _touched(changes: ChangeSet) -> list[str]:
    return list(dict.fromkeys(path for change in changes.files for path in _paths(change)))


def _lease(paths: list[str], leases: tuple[str, ...]) -> Iterator[str]:
    for path in paths:
        if not is_leasable(path):
            yield f"PUB-LEASE: {path} — not leasable"
        elif not any(contains(lease, path) for lease in leases):
            yield f"PUB-LEASE: {path} — outside the run's leases"


def _scope(paths: list[str]) -> Iterator[str]:
    has_contract = CONTRACT in paths
    for path in paths:
        if is_writable(path):
            continue
        if is_regenerated(path):
            if not has_contract:
                yield f"PUB-SCOPE: {path} — regenerated code changed without {CONTRACT}"
            continue
        yield f"PUB-SCOPE: {path} — not writable by agents"


def _tests(changes: ChangeSet, paths: list[str], labels: tuple[str, ...]) -> Iterator[str]:
    existing = [
        change.old_path or change.path
        for change in changes.files
        if change.status in _EXISTING and is_test_file(change.old_path or change.path)
    ]
    for path in uncovered_tests(tuple(dict.fromkeys(existing)), labels):
        yield f"PUB-TESTS: {path} — existing test changed without a modify-tests label"
    for path in paths:
        if is_test_infrastructure(path):
            yield f"PUB-TESTS: {path} — test infrastructure is never changed"


def _pragmas(changes: ChangeSet, labels: tuple[str, ...]) -> Iterator[str]:
    allowed = {label[len(ALLOW_LABEL) :] for label in labels if label.startswith(ALLOW_LABEL)}
    found: dict[tuple[str, str], None] = {}
    for path, line in changes.added_lines:
        hits = [pragma for pragma in PRAGMAS if pragma in line]
        # `pytest.skip(` also contains `.skip(`: report the most specific pragma only.
        for pragma in hits:
            is_part = any(pragma != other and pragma in other for other in hits)
            if not is_part and pragma not in allowed:
                found[(path, pragma)] = None
    for path, pragma in found:
        yield f"PUB-PRAGMA: {path} — adds `{pragma}`"


def _size(changes: ChangeSet) -> Iterator[str]:
    total = sum(
        change.added + change.deleted
        for change in changes.files
        if not any(is_regenerated(path) for path in _paths(change))
    )
    if total > MAX_CHANGED_LINES:
        yield (
            f"PUB-SIZE: branch — {total} changed lines, over the {MAX_CHANGED_LINES}-line cap"
            " (regenerated code not counted)"
        )


def violations(
    changes: ChangeSet, *, leases: tuple[str, ...], labels: tuple[str, ...]
) -> tuple[str, ...]:
    """Every rule broken by the branch, in rule order (PUB-LEASE, PUB-SCOPE, PUB-TESTS,
    PUB-PRAGMA, PUB-SIZE); empty when it may stand."""
    paths = _touched(changes)
    return (
        *_lease(paths, leases),
        *_scope(paths),
        *_tests(changes, paths, labels),
        *_pragmas(changes, labels),
        *_size(changes),
    )


# ---------------------------------------------------------------- the dev run's outcome
@dataclass(frozen=True)
class DevRunSummary:
    """A published dev run in counts: the branch, its diff stat and the sandbox's own test
    re-run (never the agent's claims). The reviewer's `get_task` reads it back."""

    branch: str
    files_changed: int
    insertions: int
    deletions: int
    passed: int
    failed: int
    first_failing: str | None = None


_SHOWN_TEST = re.compile(r"^[^`\x00-\x1f\x7f]{1,160}$")
_OUTCOME = re.compile(
    r"^Branch (?P<branch>agent/[A-Za-z0-9._-]{1,81}): (?P<files>\d+) files changed,"
    r" \+(?P<ins>\d+) -(?P<dels>\d+); handoff [a-z]+; tests (?P<passed>\d+) passed,"
    r" (?P<failed>\d+) failed(?:, first failing `(?P<first>[^`]{1,160})`)?\."
)


def dev_run_outcome(summary: DevRunSummary, handoff_status: str) -> str:
    """The dev run's outcome: counts, plus the first failing test id when it is plain text."""
    first = summary.first_failing
    shown = f", first failing `{first}`" if first and _SHOWN_TEST.match(first) else ""
    return (
        f"Branch {summary.branch}: {summary.files_changed} files changed,"
        f" +{summary.insertions} -{summary.deletions}; handoff {handoff_status};"
        f" tests {summary.passed} passed, {summary.failed} failed{shown}."
    )


def parse_dev_run_outcome(outcome: str | None) -> DevRunSummary | None:
    match = _OUTCOME.match(outcome or "")
    if match is None:
        return None
    return DevRunSummary(
        branch=match["branch"],
        files_changed=int(match["files"]),
        insertions=int(match["ins"]),
        deletions=int(match["dels"]),
        passed=int(match["passed"]),
        failed=int(match["failed"]),
        first_failing=match["first"],
    )
