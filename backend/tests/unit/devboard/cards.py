"""Fixture cards with recorded triage answers (`tests/fixtures/llm/devboard/triage/`).

Every in-scope answer names only paths in `tests/fixtures/llm/devboard/repo_files.txt` (the
frozen repo listing replay uses), or the workflow fails the triage run.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Card:
    title: str
    body: str
    answer: dict[str, object]


def verification(
    status: str, evidence: str, *code_refs: str, duplicate_of: str | None = None
) -> dict[str, object]:
    return {
        "status": status,
        "evidence": evidence,
        "code_refs": list(code_refs),
        "duplicate_of": duplicate_of,
    }


API_SEARCH = Card(
    title="Add a contacts search endpoint",
    body="Let users search contacts by name. Return matches ordered by last contact date.",
    answer={
        "type": "feature",
        "size": "S",
        "risk": "low",
        "areas": ["api"],
        "priority": "P2",
        "severity": "minor",
        "impact": "Users with many contacts scroll to find one; no deadline.",
        "route": "next",
        "route_reason": "A contained feature with no open question; plan it soon.",
        "verification": verification(
            "confirmed",
            "The CRM routes list contacts but take no name filter.",
            "backend/src/app/api/jobs/crm.py",
            "backend/src/app/domain/jobs/crm.py",
        ),
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
        "priority": "P2",
        "severity": "major",
        "impact": "Without backups a lost database loses every role and application.",
        "route": "later",
        "route_reason": "Out of the agents' scope; a human does this.",
        "verification": verification("unclear", "Backups are infrastructure, not in the listing."),
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
        "priority": "P3",
        "severity": "trivial",
        "impact": "Developers would see slow tests sooner.",
        "route": "later",
        "route_reason": "Nice to have.",
        "verification": verification("confirmed", "No timing summary is configured."),
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
        "priority": "P2",
        "severity": "minor",
        "impact": "Maintainers can't see what a run cost without the logs.",
        "route": "next",
        "route_reason": "Useful soon; nothing is broken.",
        "verification": verification(
            "confirmed",
            "The run node shows status and agent but no cost.",
            "web/src/features/devboard/RunNode.tsx",
        ),
        "needs_human_design": False,
        "out_of_scope": False,
        "likely_paths": ["web/src/features/devboard/", "api/openapi.yaml"],
    },
)

# Confirmed and urgent: triage routes it `now`, so a maintainer's card goes straight on to
# Plan review and its plan run starts.
URGENT_FIX = Card(
    title="Board drops cards after a failed move",
    body="When a move fails with 409 the card disappears from the board until a reload.",
    answer={
        "type": "bug",
        "size": "S",
        "risk": "low",
        "areas": ["web"],
        "priority": "P1",
        "severity": "major",
        "impact": "Everyone using the board loses sight of cards after any conflict.",
        "route": "now",
        "route_reason": "Confirmed, high impact and a small fix.",
        "verification": verification(
            "confirmed",
            "The board's move handler drops the card on error instead of restoring it.",
            "web/src/features/devboard/Board.tsx",
        ),
        "needs_human_design": False,
        "out_of_scope": False,
        "likely_paths": ["web/src/features/devboard/"],
    },
)

CARDS = (API_SEARCH, INFRA_BACKUP, TEST_PLUGIN, RUN_COST, URGENT_FIX)
