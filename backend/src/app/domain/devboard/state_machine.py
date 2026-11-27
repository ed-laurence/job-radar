"""The dev-board state machine (spec `moveTask`). The client never forces a column.

User moves are checked here; system moves (triage, agent runs, the merge webhook, the kill
switch) go through `check_system_move`. `out_of_scope` can never reach `ready`.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.devboard.model import Column, TaskRecord
from app.domain.errors import Forbidden, IllegalTransition, ValidationFailed

C = Column

# Columns a card can return to from `blocked` only via `backlog`: they are driven by runs.
_RUN_COLUMNS = frozenset({C.AGENT_DEV, C.AGENT_REVIEW})
_NEVER_BLOCKED_BY_USER = frozenset({C.DONE, C.AGENT_DEV, C.AGENT_REVIEW})
MAINTAINER_TARGETS: dict[Column, frozenset[Column]] = {
    C.TRIAGED: frozenset({C.PLAN_REVIEW}),
    C.PLAN_REVIEW: frozenset({C.READY}),
    C.READY: frozenset({C.AGENT_DEV}),
}
_OPEN_TARGETS: dict[Column, frozenset[Column]] = {
    C.OUT_OF_SCOPE: frozenset({C.BACKLOG}),
    C.TRIAGED: frozenset({C.BACKLOG}),
    C.PLAN_REVIEW: frozenset({C.BACKLOG}),
    C.READY: frozenset({C.BACKLOG}),
}
SYSTEM_MOVES: dict[Column, frozenset[Column]] = {
    C.BACKLOG: frozenset({C.TRIAGED, C.OUT_OF_SCOPE}),
    C.TRIAGED: frozenset({C.BACKLOG}),
    C.OUT_OF_SCOPE: frozenset({C.BACKLOG}),
    C.PLAN_REVIEW: frozenset({C.READY}),
    C.AGENT_DEV: frozenset({C.AGENT_REVIEW}),
    C.AGENT_REVIEW: frozenset({C.HUMAN_REVIEW, C.CHANGES_REQUESTED}),
    C.CHANGES_REQUESTED: frozenset({C.AGENT_DEV}),
    C.HUMAN_REVIEW: frozenset({C.DONE}),
}


@dataclass(frozen=True)
class MoveCheck:
    """A legal user move. `is_reorder` means the column stays the same."""

    is_reorder: bool
    needs_plan_approval: bool


def user_targets(task: TaskRecord, *, is_maintainer: bool) -> frozenset[Column]:
    """Every column this account may move the card to (including its own, for a reorder)."""
    current = task.column
    targets: set[Column] = {current}
    targets |= _OPEN_TARGETS.get(current, frozenset())
    if is_maintainer:
        targets |= MAINTAINER_TARGETS.get(current, frozenset())
    if current not in _NEVER_BLOCKED_BY_USER:
        targets.add(C.BLOCKED)
    if current is C.BLOCKED:
        targets.add(C.BACKLOG)
        if task.blocked_from is not None and task.blocked_from not in _RUN_COLUMNS:
            targets.add(task.blocked_from)
    if current is C.OUT_OF_SCOPE:
        targets.discard(C.READY)
    return frozenset(targets)


def check_user_move(
    task: TaskRecord, target: Column, *, is_maintainer: bool, reason: str | None
) -> MoveCheck:
    """Raise `Forbidden` (maintainer-only move), `IllegalTransition` or `ValidationFailed`."""
    allowed = user_targets(task, is_maintainer=is_maintainer)
    if target not in allowed:
        maintainer_only = MAINTAINER_TARGETS.get(task.column, frozenset())
        if not is_maintainer and target in maintainer_only:
            raise Forbidden("Only a maintainer can make this move.")
        raise IllegalTransition(
            "That move isn't allowed from this column.", allowed=_ordered(allowed)
        )
    is_reorder = target is task.column
    if target is C.BLOCKED and not is_reorder and not (reason and reason.strip()):
        raise ValidationFailed("Say why the card is blocked.")
    needs_approval = task.column is C.PLAN_REVIEW and target is C.READY
    return MoveCheck(is_reorder=is_reorder, needs_plan_approval=needs_approval)


def check_system_move(task: TaskRecord, target: Column) -> None:
    """System moves: the fixed table plus the kill switch (anything open to `blocked`)."""
    if target is C.READY and task.column is C.OUT_OF_SCOPE:
        raise IllegalTransition("Out-of-scope cards can never be made ready.")
    if target is C.BLOCKED and task.column not in {C.DONE, C.BLOCKED}:
        return
    if target not in SYSTEM_MOVES.get(task.column, frozenset()):
        raise IllegalTransition("That move isn't allowed from this column.")


# An accepted task-chat edit leaves a card's triage stale, and past triage its plan approval
# too. The edit path re-triages these columns itself; any other card goes back to `backlog`
# (plan approval withdrawn) and is triaged again. `done` is final: open a new card instead.
RETRIAGE_IN_PLACE = frozenset({C.BACKLOG, C.TRIAGED, C.OUT_OF_SCOPE})


def edit_returns_to_backlog(task: TaskRecord) -> bool:
    """Whether accepting an edit sends the card back to `backlog`. Raises for `done`."""
    if task.column is C.DONE:
        raise IllegalTransition("Done cards can't be edited. Open a new card instead.")
    return task.column not in RETRIAGE_IN_PLACE


def _ordered(columns: frozenset[Column]) -> list[str]:
    return [column.value for column in Column if column in columns]
