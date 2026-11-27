"""Fixture cards with recorded triage answers (`tests/fixtures/llm/devboard/triage/`)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Card:
    title: str
    body: str
    answer: dict[str, object]


API_SEARCH = Card(
    title="Add a contacts search endpoint",
    body="Let users search contacts by name. Return matches ordered by last contact date.",
    answer={
        "type": "feature",
        "size": "S",
        "risk": "low",
        "areas": ["api"],
        "acceptance_criteria": [
            {
                "test_id": "backend/tests/unit/test_contacts_search.py::matches by name",
                "expectation": "GET /contacts?q=ali returns only contacts whose name matches",
            },
            {
                "test_id": "backend/tests/unit/test_contacts_search.py::orders by last contact",
                "expectation": "results are ordered by last_contacted_at, newest first",
            },
        ],
        "needs_human_design": False,
        "out_of_scope": False,
        "likely_paths": ["backend/src/app/api/", "backend/src/app/domain/"],
    },
)

INFRA_BACKUP = Card(
    title="Add a nightly database backup job",
    body="Needs a new Cloud Scheduler trigger and an OpenTofu module for the bucket.",
    answer={
        "type": "chore",
        "size": "M",
        "risk": "medium",
        "areas": [],
        "acceptance_criteria": [
            {
                "test_id": "backend/tests/unit/test_backup.py::schedules nightly",
                "expectation": "a scheduler trigger exists for the backup job",
            }
        ],
        "needs_human_design": False,
        "out_of_scope": True,
        "out_of_scope_reason": "Needs infrastructure (Cloud Scheduler, OpenTofu).",
        "likely_paths": ["infra/modules/backup/"],
    },
)

# The model calls this in scope; the path-risk map and write scope overrule it.
TEST_PLUGIN = Card(
    title="Time every test",
    body="Print the slowest ten tests after each run.",
    answer={
        "type": "test",
        "size": "S",
        "risk": "low",
        "areas": ["api"],
        "acceptance_criteria": [
            {
                "test_id": "backend/tests/unit/test_timing.py::prints slowest tests",
                "expectation": "the summary lists ten test ids with durations",
            }
        ],
        "needs_human_design": False,
        "out_of_scope": False,
        "likely_paths": ["backend/tests/conftest.py"],
    },
)

# Low risk per the model, but the contract is a hotspot: code raises it to high.
RUN_COST = Card(
    title="Show agent cost on the run card",
    body="Add the run's cost to the API and show it on the board's run card.",
    answer={
        "type": "feature",
        "size": "M",
        "risk": "low",
        "areas": ["web", "api"],
        "acceptance_criteria": [
            {
                "test_id": "web/tests/board.spec.ts::run card shows cost",
                "expectation": "the run card shows cost_usd formatted as dollars",
            }
        ],
        "needs_human_design": False,
        "out_of_scope": False,
        "likely_paths": ["web/src/features/board/", "api/openapi.yaml"],
    },
)

CARDS = (API_SEARCH, INFRA_BACKUP, TEST_PLUGIN, RUN_COST)
