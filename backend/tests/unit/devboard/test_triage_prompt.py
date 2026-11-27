"""The triage prompt tells the model the exact write scope and asks it to verify, rate and route
the card using only repo paths it was shown. Triage writes no acceptance criteria: the
planner does (so the schema has no place for them)."""

from __future__ import annotations

import re
from typing import cast

import pytest
from pydantic import ValidationError

from app.agents.triage.prompt import INSTRUCTIONS, SYSTEM
from app.agents.triage.schema import TriageOutput
from app.domain.devboard.paths import WRITE_ALLOWLIST, is_writable
from tests.unit.devboard import cards

PROMPT = f"{SYSTEM}\n{INSTRUCTIONS}"


def test_prompt_names_every_writable_path_exactly() -> None:
    for allowed in WRITE_ALLOWLIST:
        assert allowed in PROMPT, allowed


def test_every_repo_path_the_prompt_names_is_writable() -> None:
    named = re.findall(r"(?:backend|web|api|docs)/[\w./-]*", PROMPT)
    assert named
    for path in named:
        assert is_writable(path.rstrip(".")), path


def test_prompt_asks_to_verify_rate_and_route() -> None:
    for word in ("confirmed", "not_reproduced", "unclear", "duplicate", "evidence", "code_refs"):
        assert word in INSTRUCTIONS, word
    for level in ("P0", "P1", "P2", "P3", "critical", "major", "minor", "trivial"):
        assert level in INSTRUCTIONS, level
    for route in ("now", "next", "later", "route_reason"):
        assert route in INSTRUCTIONS, route


def test_prompt_limits_paths_to_the_listing_and_forbids_criteria() -> None:
    assert "repo_files" in INSTRUCTIONS
    assert "open_cards" in INSTRUCTIONS
    assert "path_problems" in INSTRUCTIONS
    assert "Never write acceptance criteria" in INSTRUCTIONS


def test_the_schema_has_no_acceptance_criteria() -> None:
    assert "acceptance_criteria" not in TriageOutput.model_json_schema()["properties"]
    answer = cards.API_SEARCH.answer | {
        "acceptance_criteria": [{"test_id": "backend/tests/unit/test_x.py::y", "expectation": "z"}]
    }
    with pytest.raises(ValidationError):
        TriageOutput.model_validate(answer)


@pytest.mark.parametrize("card", cards.CARDS, ids=lambda card: card.title)
def test_every_fixture_answer_fits_the_schema(card: cards.Card) -> None:
    TriageOutput.model_validate(card.answer)


def _with_verification(**changes: object) -> dict[str, object]:
    found = cast("dict[str, object]", cards.API_SEARCH.answer["verification"])
    return cards.API_SEARCH.answer | {"verification": found | changes}


def test_duplicate_of_is_a_card_ref_set_only_for_duplicates() -> None:
    ok = TriageOutput.model_validate(_with_verification(status="duplicate", duplicate_of="C2"))
    assert ok.verification.duplicate_of == "C2"
    for bad in (
        {"status": "duplicate", "duplicate_of": None},
        {"status": "confirmed", "duplicate_of": "C2"},
        {"status": "duplicate", "duplicate_of": "card 2"},
    ):
        with pytest.raises(ValidationError):
            TriageOutput.model_validate(_with_verification(**bad))


@pytest.mark.parametrize(
    ("field", "value"),
    [("priority", "P4"), ("severity", "blocker"), ("route", "soon"), ("type", "epic")],
)
def test_ratings_outside_the_vocabulary_are_refused(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        TriageOutput.model_validate(cards.API_SEARCH.answer | {field: value})


def test_security_and_performance_are_task_types() -> None:
    for kind in ("security", "performance"):
        assert TriageOutput.model_validate(cards.API_SEARCH.answer | {"type": kind}).type == kind
