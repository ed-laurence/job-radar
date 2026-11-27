"""Task chat proposals: the field diffs Claude proposes for a card, and how an accepted one
becomes an ordinary task edit (spec `acceptTaskChatProposal`).

A card has a title and a body. In chat the body is two fields: the description, and the
written acceptance criteria, which are the bullets under a `## Acceptance criteria` heading at
the end of the body. Triage reads both and turns the criteria into executable test ids. On
accept the body is rebuilt from the two, so the card stays one plain text field.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import cast

from app.domain.devboard.model import TaskEdit, TaskRecord
from app.domain.errors import Conflict, ValidationFailed

CRITERIA_HEADING = "## Acceptance criteria"
MAX_TITLE = 200
MAX_BODY = 8000
MAX_CRITERIA = 30
MAX_CRITERION = 1000
MAX_REASON = 1000

_HEADING = re.compile(
    r"^[ \t]{0,3}(?:#{1,6}[ \t]*acceptance criteria[ \t]*:?|acceptance criteria[ \t]*:)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_BULLET = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+")


class ProposalStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True)
class TextChange:
    before: str
    after: str


@dataclass(frozen=True)
class CriteriaChange:
    before: tuple[str, ...]
    after: tuple[str, ...]


@dataclass(frozen=True)
class EditChanges:
    """Only the fields that change."""

    title: TextChange | None = None
    description: TextChange | None = None
    acceptance_criteria: CriteriaChange | None = None

    @property
    def is_empty(self) -> bool:
        return self.title is None and self.description is None and self.acceptance_criteria is None

    @property
    def field_names(self) -> tuple[str, ...]:
        named = (
            ("title", self.title),
            ("description", self.description),
            ("acceptance criteria", self.acceptance_criteria),
        )
        return tuple(name for name, change in named if change is not None)


@dataclass(frozen=True)
class ProposedEdit:
    """What the model asked for. `None` leaves a field as it is."""

    reason: str
    title: str | None = None
    description: str | None = None
    acceptance_criteria: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ProposalRecord:
    id: uuid.UUID
    task_id: uuid.UUID
    status: ProposalStatus
    reason: str
    changes: EditChanges
    base_version: int
    created_at: datetime
    decided_at: datetime | None = None


def split_body(body: str) -> tuple[str, tuple[str, ...]]:
    """`(description, criteria)`. The last acceptance-criteria heading starts the section, which
    runs to the end of the body; each non-empty line in it is one criterion."""
    headings = list(_HEADING.finditer(body))
    if not headings:
        return body.strip(), ()
    last = headings[-1]
    lines = body[last.end() :].splitlines()
    criteria = tuple(_BULLET.sub("", line).strip() for line in lines if line.strip())
    return body[: last.start()].strip(), criteria


def compose_body(description: str, criteria: tuple[str, ...]) -> str:
    if not criteria:
        return description.strip()
    bullets = "\n".join(f"- {item}" for item in criteria)
    return f"{description.strip()}\n\n{CRITERIA_HEADING}\n{bullets}".strip()


def describe(changes: EditChanges) -> str:
    """For the step stream: "Proposed an edit to the title and description"."""
    names = changes.field_names
    joined = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
    return f"Proposed an edit to the {joined}."


def diff_edit(task: TaskRecord, proposed: ProposedEdit) -> EditChanges:
    """The field diffs against the card as it is now. Raises `ValidationFailed` (the model sees
    the detail and can try again) for an empty, oversized or no-op proposal."""
    if not proposed.reason.strip() or len(proposed.reason) > MAX_REASON:
        raise ValidationFailed(f"Give a reason of 1 to {MAX_REASON} characters.")
    description, criteria = split_body(task.body)
    title = _text_change(task.title, proposed.title)
    if title is not None and not 1 <= len(title.after) <= MAX_TITLE:
        raise ValidationFailed(f"A title is 1 to {MAX_TITLE} characters.")
    changes = EditChanges(
        title=title,
        description=_text_change(description, proposed.description),
        acceptance_criteria=_criteria_change(criteria, proposed.acceptance_criteria),
    )
    if changes.is_empty:
        raise ValidationFailed("The proposal doesn't change anything on the card.")
    body = _new_body(description, criteria, changes)
    if body is not None and len(body) > MAX_BODY:
        raise ValidationFailed(f"The card's body would be over {MAX_BODY} characters.")
    return changes


def check_fresh(task: TaskRecord, changes: EditChanges) -> None:
    """Every field the proposal changes still reads as it did when it was proposed."""
    description, criteria = split_body(task.body)
    is_stale = (
        (changes.title is not None and changes.title.before != task.title)
        or (changes.description is not None and changes.description.before != description)
        or (
            changes.acceptance_criteria is not None
            and changes.acceptance_criteria.before != criteria
        )
    )
    if is_stale:
        raise Conflict(
            "The card changed since this edit was proposed. Ask for a fresh proposal.",
            current_version=task.version,
        )


def to_task_edit(task: TaskRecord, changes: EditChanges, version: int) -> TaskEdit:
    description, criteria = split_body(task.body)
    title = changes.title.after if changes.title is not None else None
    return TaskEdit(version=version, title=title, body=_new_body(description, criteria, changes))


def _new_body(description: str, criteria: tuple[str, ...], changes: EditChanges) -> str | None:
    if changes.description is None and changes.acceptance_criteria is None:
        return None
    new_description = changes.description.after if changes.description else description
    new_criteria = changes.acceptance_criteria.after if changes.acceptance_criteria else criteria
    return compose_body(new_description, new_criteria)


def _text_change(current: str, proposed: str | None) -> TextChange | None:
    if proposed is None:
        return None
    clean = proposed.strip()
    return None if clean == current.strip() else TextChange(before=current, after=clean)


def _criteria_change(
    current: tuple[str, ...], proposed: tuple[str, ...] | None
) -> CriteriaChange | None:
    if proposed is None:
        return None
    # One criterion is one bullet line, so line breaks inside one are folded.
    clean = tuple(" ".join(item.split()) for item in proposed if item.strip())
    if len(clean) > MAX_CRITERIA or any(len(item) > MAX_CRITERION for item in clean):
        raise ValidationFailed(
            f"At most {MAX_CRITERIA} acceptance criteria of {MAX_CRITERION} characters each."
        )
    return None if clean == current else CriteriaChange(before=current, after=clean)


def changes_to_dict(changes: EditChanges) -> dict[str, object]:
    """The JSON shape of the spec's `TaskEditChanges` (also how it is stored)."""
    found: dict[str, object] = {}
    for name, text in (("title", changes.title), ("description", changes.description)):
        if text is not None:
            found[name] = {"before": text.before, "after": text.after}
    if changes.acceptance_criteria is not None:
        found["acceptance_criteria"] = {
            "before": list(changes.acceptance_criteria.before),
            "after": list(changes.acceptance_criteria.after),
        }
    return found


def changes_from_dict(value: dict[str, object]) -> EditChanges:
    def text(key: str) -> TextChange | None:
        item = cast("dict[str, str] | None", value.get(key))
        return None if item is None else TextChange(item["before"], item["after"])

    criteria = cast("dict[str, list[str]] | None", value.get("acceptance_criteria"))
    return EditChanges(
        title=text("title"),
        description=text("description"),
        acceptance_criteria=None
        if criteria is None
        else CriteriaChange(tuple(criteria["before"]), tuple(criteria["after"])),
    )
