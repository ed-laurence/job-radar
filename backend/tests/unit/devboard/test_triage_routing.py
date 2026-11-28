"""Code caps triage's route (out of scope, duplicate or not reproduced -> later; unclear, needs
human design or no maintainer -> at most next), checks every path it names against the repo,
and accepts a duplicate only of an open card."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.domain.devboard.model import Priority, Route, Severity, Verification, VerificationStatus
from app.domain.devboard.triage_policy import (
    MAX_CODE_REFS,
    MAX_IMPACT_CHARS,
    MAX_ROUTE_REASON_CHARS,
    effective_route,
    finalise,
    triage_problem,
)
from tests.unit.devboard.workflow_harness import FILES, decision

NOW = datetime(2026, 11, 25, tzinfo=UTC)
OTHER = uuid.UUID(int=77)


def _verified(status: VerificationStatus, duplicate_of: uuid.UUID | None = None) -> Verification:
    return Verification(status, "Checked the code.", (), duplicate_of)


@pytest.mark.parametrize("route", list(Route))
def test_a_confirmed_answer_keeps_the_models_route(route: Route) -> None:
    assert effective_route(decision(route=route), in_scope=True).route is route


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (VerificationStatus.DUPLICATE, Route.LATER),
        (VerificationStatus.NOT_REPRODUCED, Route.LATER),
        (VerificationStatus.UNCLEAR, Route.NEXT),
    ],
)
def test_verification_caps_the_route(status: VerificationStatus, expected: Route) -> None:
    routed = effective_route(
        decision(route=Route.NOW, verification=_verified(status)), in_scope=True
    )
    assert routed.route is expected
    assert "Suggested now" in routed.reason


def test_caps_never_raise_a_route() -> None:
    unclear = decision(route=Route.LATER, verification=_verified(VerificationStatus.UNCLEAR))
    assert effective_route(unclear, in_scope=True).route is Route.LATER


def test_a_design_question_caps_now_at_next() -> None:
    routed = effective_route(decision(route=Route.NOW, needs_human_design=True), in_scope=True)
    assert routed.route is Route.NEXT
    assert "design" in routed.reason


def test_out_of_scope_is_later() -> None:
    assert effective_route(decision(route=Route.NOW), in_scope=False).route is Route.LATER


def test_now_stands_whoever_made_the_card() -> None:
    # No actor rule: the maintainer's and the tester's cards route the same way.
    assert effective_route(decision(route=Route.NOW), in_scope=True).route is Route.NOW


def test_finalise_stores_the_ratings_and_the_effective_route() -> None:
    answer = decision(
        priority=Priority.P1,
        severity=Severity.MAJOR,
        route=Route.NOW,
        verification=_verified(VerificationStatus.UNCLEAR),
    )
    record = finalise(uuid.UUID(int=5), answer, NOW)
    assert (record.priority, record.severity) == (Priority.P1, Severity.MAJOR)
    assert record.route is Route.NEXT
    assert record.verification.status is VerificationStatus.UNCLEAR
    assert record.in_scope


def test_finalise_clips_long_model_text_to_the_spec_limits() -> None:
    refs = tuple(f"web/src/features/devboard/F{index}.tsx" for index in range(30))
    answer = decision(
        impact="x" * 2000,
        route_reason="y" * 2000,
        verification=Verification(VerificationStatus.CONFIRMED, " e " * 1000, refs),
    )
    record = finalise(uuid.UUID(int=5), answer, NOW)
    assert len(record.impact) == MAX_IMPACT_CHARS
    assert len(record.route_reason) == MAX_ROUTE_REASON_CHARS
    assert len(record.verification.code_refs) == MAX_CODE_REFS
    assert len(record.verification.evidence) <= 1000


def test_finalise_keeps_the_likely_paths_for_the_planner() -> None:
    answer = decision(
        likely_paths=("web/src/features/devboard/BoardMetrics.tsx", "backend/src/app/domain/")
    )
    record = finalise(uuid.UUID(int=5), answer, NOW)
    assert record.likely_paths == (
        "web/src/features/devboard/BoardMetrics.tsx",
        "backend/src/app/domain/",
    )


def test_finalise_keeps_no_paths_when_the_model_named_none() -> None:
    # Area defaults decide risk and scope, but they aren't the model's paths.
    record = finalise(uuid.UUID(int=5), decision(likely_paths=()), NOW)
    assert record.likely_paths == ()


def test_known_paths_and_no_duplicate_pass() -> None:
    assert triage_problem(decision(), FILES, ()) is None


def test_invented_paths_are_named_at_most_five() -> None:
    invented = tuple(f"web/src/components/x{index}/A.tsx" for index in range(7))
    answer = decision(likely_paths=invented[:4])
    answer = replace(
        answer, verification=Verification(VerificationStatus.CONFIRMED, "e", invented[4:])
    )
    problem = triage_problem(answer, FILES, ())
    assert problem is not None
    assert problem.startswith("Triage named paths that are not in the repo: ")
    assert "web/src/components/x4/A.tsx" in problem  # code refs are checked first
    assert "web/src/components/x3/A.tsx" not in problem
    assert "(and 2 more)" in problem


def test_paths_are_not_checked_when_files_is_none() -> None:
    answer = decision(likely_paths=("infra/modules/backup/",))
    assert triage_problem(answer, None, ()) is None


def test_a_duplicate_must_name_an_open_card() -> None:
    duplicate = decision(verification=_verified(VerificationStatus.DUPLICATE, OTHER))
    assert triage_problem(duplicate, FILES, (OTHER,)) is None
    problem = triage_problem(duplicate, FILES, (uuid.UUID(int=78),))
    assert problem is not None
    assert "not an open card" in problem


@pytest.mark.parametrize(
    "verification",
    [
        _verified(VerificationStatus.DUPLICATE),
        _verified(VerificationStatus.CONFIRMED, OTHER),
    ],
    ids=["duplicate-without-card", "card-without-duplicate"],
)
def test_duplicate_of_goes_with_the_duplicate_status(verification: Verification) -> None:
    assert triage_problem(decision(verification=verification), FILES, (OTHER,)) is not None
