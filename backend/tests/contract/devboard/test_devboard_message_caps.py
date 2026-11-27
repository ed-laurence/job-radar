"""Coordinator message caps: oversized messages are refused, never truncated."""

from __future__ import annotations

import json

import pytest

from app.domain.devboard.messages import (
    BRIEF_TOKENS,
    HANDOFF_TOKENS,
    REQUEST_HELP_TOKENS,
    Brief,
    Findings,
    Handoff,
    RequestHelp,
    estimate_tokens,
    parse_message,
)
from app.domain.errors import ValidationFailed


def _handoff(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "status": "done",
        "summary": "Added the search endpoint and its tests.",
        "changed_paths": ["backend/src/app/api/contacts.py"],
        "tests": {"passed": 12, "failed": 0},
        "rubric_checked": ["ARCH-03", "TEST-01"],
        "open_questions": [],
    }
    return base | changes


def test_a_handoff_within_the_caps_is_accepted_unchanged() -> None:
    raw = json.dumps(_handoff())
    handoff = parse_message(Handoff, raw)
    assert handoff.summary == "Added the search endpoint and its tests."
    assert estimate_tokens(handoff.model_dump_json()) <= HANDOFF_TOKENS


def test_an_oversized_handoff_is_refused_not_cut() -> None:
    paths = [f"web/src/features/board/component_{n}.tsx" for n in range(90)]
    with pytest.raises(ValidationFailed) as error:
        parse_message(Handoff, json.dumps(_handoff(changed_paths=paths)))
    assert "cap is 400" in error.value.errors[0].message


@pytest.mark.parametrize(
    "changes",
    [
        {"summary": " ".join(["word"] * 81)},
        {"open_questions": ["a?", "b?", "c?", "d?"]},
        {"summary": "Fixed it:\n```python\nprint(1)\n```"},
        {"summary": "@@ -1,2 +1,2 @@ changed"},
        {"rubric_checked": ["looks fine"]},
        {"token_cap": 100000},
    ],
)
def test_handoff_field_rules(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationFailed):
        parse_message(Handoff, json.dumps(_handoff(**changes)))


def test_brief_and_request_help_caps() -> None:
    brief = {
        "task_id": "t1",
        "subtask_id": "s1",
        "agent": "backend-engineer",
        "goal": "Add the endpoint.",
        "lease_paths": ["backend/src/app/api/"],
    }
    assert parse_message(Brief, json.dumps(brief)).agent == "backend-engineer"
    long_goal = "x " * (BRIEF_TOKENS * 2)
    with pytest.raises(ValidationFailed):
        parse_message(Brief, json.dumps(brief | {"goal": long_goal}))
    help_ok = {"run_id": "r1", "question": "Which column order should the API use?"}
    assert parse_message(RequestHelp, json.dumps(help_ok)).blocking is True
    with pytest.raises(ValidationFailed):
        too_long = "why " * REQUEST_HELP_TOKENS
        parse_message(RequestHelp, json.dumps(help_ok | {"question": too_long}))


def _finding(words: int = 10) -> dict[str, object]:
    return {
        "rubric_id": "ARCH-03",
        "severity": "MAJOR",
        "path": "backend/src/app/api/x.py",
        "text": " ".join(["word"] * words),
    }


def test_findings_are_capped_in_count_and_length() -> None:
    ten = parse_message(Findings, json.dumps({"reviewer": "gemini", "findings": [_finding()] * 10}))
    assert len(ten.findings) == 10
    with pytest.raises(ValidationFailed):
        parse_message(Findings, json.dumps({"reviewer": "gemini", "findings": [_finding()] * 11}))
    with pytest.raises(ValidationFailed):
        parse_message(Findings, json.dumps({"reviewer": "gemini", "findings": [_finding(61)]}))
