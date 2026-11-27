"""The recorded task-chat conversation (`tests/fixtures/llm/devboard/task_chat/`) and the triage
of the card after its proposal is accepted (`.../devboard/triage/`).

The chat is about `cards.API_SEARCH` while it is in Triaged with no plan: Claude reads the card,
proposes written acceptance criteria, then answers. Fixtures are keyed by the prompt, so the
same card, column and message replay the same turns.
"""

from __future__ import annotations

from app.domain.devboard.proposals import compose_body
from tests.unit.devboard.cards import API_SEARCH

MESSAGE = "Can you make the acceptance criteria testable?"
CRITERIA = (
    "Searching by part of a name returns only the matching contacts",
    "Results are ordered by last contact date, newest first",
    "An empty search returns every contact",
)
REASON = "The card has no written acceptance criteria an agent could test."
REPLY = (
    "I proposed three acceptance criteria: partial name matches, ordering by last contact date, "
    "and an empty search. Accept them and triage will turn them into tests."
)
STEP_SUMMARIES = ("Read the card.", "Proposed an edit to the acceptance criteria.")

TURNS: tuple[dict[str, object], ...] = (
    {"text": "Reading the card first.", "call": ("toolu_1", "get_task", {})},
    {
        "text": "Proposing written criteria.",
        "call": (
            "toolu_2",
            "propose_task_edit",
            {
                "title": None,
                "description": None,
                "acceptance_criteria": list(CRITERIA),
                "reason": REASON,
            },
        ),
    },
    {"text": '{"reply": "' + REPLY + '"}', "call": None},
)

EDITED_BODY = compose_body(API_SEARCH.body, CRITERIA)
EDITED_TRIAGE: dict[str, object] = {
    "type": "feature",
    "size": "S",
    "risk": "low",
    "areas": ["api"],
    "acceptance_criteria": [
        {
            "test_id": "backend/tests/unit/test_contacts_search.py::matches part of a name",
            "expectation": "GET /contacts?q=ali returns only contacts whose name contains ali",
        },
        {
            "test_id": "backend/tests/unit/test_contacts_search.py::orders by last contact",
            "expectation": "results are ordered by last_contacted_at, newest first",
        },
        {
            "test_id": "backend/tests/unit/test_contacts_search.py::empty search lists all",
            "expectation": "GET /contacts?q= returns every contact",
        },
    ],
    "needs_human_design": False,
    "out_of_scope": False,
    "likely_paths": ["backend/src/app/api/", "backend/src/app/domain/"],
}
