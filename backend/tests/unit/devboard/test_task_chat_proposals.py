"""Task chat proposals: body split and rebuild, field diffs, staleness, the task edit they
become, and the state-machine rule for edits on cards past triage."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.domain.devboard.model import Column, TaskRecord
from app.domain.devboard.proposals import (
    CriteriaChange,
    EditChanges,
    ProposedEdit,
    TextChange,
    changes_from_dict,
    changes_to_dict,
    check_fresh,
    compose_body,
    describe,
    diff_edit,
    split_body,
    to_task_edit,
)
from app.domain.devboard.state_machine import edit_returns_to_backlog
from app.domain.errors import Conflict, IllegalTransition, ValidationFailed

NOW = datetime(2026, 11, 25, 1, 0, tzinfo=UTC)
DESCRIPTION = "Show a hint when there are no contacts."
BODY = f"{DESCRIPTION}\n\n## Acceptance criteria\n- shows a hint\n- links to import"


def _task(body: str = BODY, column: Column = Column.TRIAGED, version: int = 3) -> TaskRecord:
    return TaskRecord(
        uuid.UUID(int=1), "Empty state", body, column, "U", version, uuid.UUID(int=2), NOW, NOW
    )


def test_split_body_reads_the_last_acceptance_criteria_section() -> None:
    assert split_body(BODY) == (
        "Show a hint when there are no contacts.",
        ("shows a hint", "links to import"),
    )
    numbered = "Do it.\nAcceptance criteria:\n1. first\n2) second\n\n* third"
    assert split_body(numbered) == ("Do it.", ("first", "second", "third"))
    assert split_body("  No section here.  ") == ("No section here.", ())


def test_compose_body_round_trips_through_split() -> None:
    body = compose_body("Describe it.", ("one", "two"))
    assert body == "Describe it.\n\n## Acceptance criteria\n- one\n- two"
    assert split_body(body) == ("Describe it.", ("one", "two"))
    assert compose_body("Only text. ", ()) == "Only text."


def test_diff_keeps_only_the_fields_that_change() -> None:
    proposed = ProposedEdit(
        reason="Clearer.",
        title="Empty state",  # unchanged: dropped
        description="Show a hint and an import link when the list is empty.",
        acceptance_criteria=("shows a hint", "links to import"),  # unchanged: dropped
    )
    changes = diff_edit(_task(), proposed)
    assert changes.title is None
    assert changes.acceptance_criteria is None
    assert changes.description == TextChange(
        before="Show a hint when there are no contacts.",
        after="Show a hint and an import link when the list is empty.",
    )
    assert describe(changes) == "Proposed an edit to the description."


def test_diff_folds_line_breaks_and_drops_empty_criteria() -> None:
    proposed = ProposedEdit(reason="r", acceptance_criteria=("a\n  b", "  ", "c"))
    changes = diff_edit(_task(), proposed)
    assert changes.acceptance_criteria == CriteriaChange(
        before=("shows a hint", "links to import"), after=("a b", "c")
    )


@pytest.mark.parametrize(
    "proposed",
    [
        ProposedEdit(reason="Nothing new.", title="Empty state"),
        ProposedEdit(reason=" ", title="New title"),
        ProposedEdit(reason="r", title=" "),
        ProposedEdit(reason="r", title="x" * 201),
        ProposedEdit(reason="r", description="x" * 8001),
        ProposedEdit(reason="r", acceptance_criteria=tuple(f"c{i}" for i in range(31))),
        ProposedEdit(reason="r", acceptance_criteria=("x" * 1001,)),
    ],
)
def test_diff_refuses_empty_oversized_and_no_op_proposals(proposed: ProposedEdit) -> None:
    with pytest.raises(ValidationFailed):
        diff_edit(_task(), proposed)


def test_a_title_only_edit_leaves_the_body_alone() -> None:
    changes = EditChanges(title=TextChange("Empty state", "Empty states"))
    edit = to_task_edit(_task(), changes, version=3)
    assert (edit.version, edit.title, edit.body) == (3, "Empty states", None)


def test_a_criteria_edit_rebuilds_the_body_around_the_description() -> None:
    changes = EditChanges(
        acceptance_criteria=CriteriaChange(("shows a hint", "links to import"), ("only one",))
    )
    edit = to_task_edit(_task(), changes, version=3)
    assert edit.title is None
    assert edit.body == f"{DESCRIPTION}\n\n## Acceptance criteria\n- only one"
    both = replace(changes, description=TextChange(DESCRIPTION, "New."))
    assert to_task_edit(_task(), both, 3).body == "New.\n\n## Acceptance criteria\n- only one"


def test_a_proposal_is_stale_once_a_field_it_changes_has_moved_on() -> None:
    changes = diff_edit(_task(), ProposedEdit(reason="r", title="Better title"))
    check_fresh(_task(version=9), changes)  # other edits are fine: only its fields count
    with pytest.raises(Conflict) as stale:
        check_fresh(replace(_task(version=9), title="Someone else's title"), changes)
    assert stale.value.current_version == 9


def test_changes_round_trip_through_their_json_shape() -> None:
    changes = EditChanges(
        title=TextChange("a", "b"), acceptance_criteria=CriteriaChange((), ("x", "y"))
    )
    dumped = changes_to_dict(changes)
    assert set(dumped) == {"title", "acceptance_criteria"}
    assert changes_from_dict(dumped) == changes
    assert describe(changes) == "Proposed an edit to the title and acceptance criteria."


@pytest.mark.parametrize("column", [Column.BACKLOG, Column.TRIAGED, Column.OUT_OF_SCOPE])
def test_edits_before_planning_are_triaged_in_place(column: Column) -> None:
    assert edit_returns_to_backlog(_task(column=column)) is False


@pytest.mark.parametrize(
    "column",
    [
        Column.PLAN_REVIEW,
        Column.READY,
        Column.BLOCKED,
        Column.AGENT_DEV,
        Column.AGENT_REVIEW,
        Column.CHANGES_REQUESTED,
        Column.HUMAN_REVIEW,
    ],
)
def test_edits_past_triage_send_the_card_back_to_backlog(column: Column) -> None:
    assert edit_returns_to_backlog(_task(column=column)) is True


def test_done_cards_cannot_be_edited() -> None:
    with pytest.raises(IllegalTransition):
        edit_returns_to_backlog(_task(column=Column.DONE))
