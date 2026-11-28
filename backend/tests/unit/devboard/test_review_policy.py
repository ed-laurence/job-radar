"""Code's rules over a reviewer's answer: citations, changed paths, duplicates, the cap and the
verdict by round (docs/features/agent-review.md section 2)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.domain.devboard.model import ReviewFinding, ReviewSeverity, ReviewVerdict
from app.domain.devboard.review_policy import MAX_FINDINGS, diff_paths, finalise
from app.domain.ports.devboard import ReviewDraft

RUBRIC = frozenset({"ARCH-03", "SEC-06"})
CHANGED = frozenset({"backend/src/app/api/devboard/tasks.py", "web/src/features/x.tsx"})
PATH = "backend/src/app/api/devboard/tasks.py"


def finding(severity: ReviewSeverity = ReviewSeverity.BLOCKER, **changes: object) -> ReviewFinding:
    base = ReviewFinding(severity, path=PATH, evidence="A rule in the route.", line=12)
    return replace(base, **changes)


def run(
    *findings: ReviewFinding,
    review_round: int = 1,
    changed: frozenset[str] | None = CHANGED,
) -> tuple[tuple[ReviewFinding, ...], ReviewVerdict]:
    draft = ReviewDraft(summary="Checked.", findings=findings)
    return finalise(draft, review_round=review_round, rubric_ids=RUBRIC, changed_paths=changed)


@pytest.mark.parametrize(
    "citation",
    [{"rubric_id": "ARCH-03"}, {"test_id": "tests/unit/x.py::test_y"}, {"repro": "POST twice."}],
)
def test_a_cited_blocker_stays_a_blocker(citation: dict[str, str]) -> None:
    findings, verdict = run(replace(finding(), **citation))
    assert findings[0].severity is ReviewSeverity.BLOCKER
    assert not findings[0].downgraded
    assert verdict is ReviewVerdict.CHANGES_REQUESTED


def test_an_uncited_blocker_is_downgraded_to_a_nit() -> None:
    findings, verdict = run(finding())
    assert findings[0].severity is ReviewSeverity.NIT
    assert findings[0].downgraded
    assert verdict is ReviewVerdict.APPROVED


def test_a_rubric_id_not_in_the_rubric_is_no_citation() -> None:
    findings, verdict = run(finding(rubric_id="ZZZ-99"))
    assert findings[0].rubric_id is None
    assert findings[0].downgraded
    assert verdict is ReviewVerdict.APPROVED


def test_a_finding_on_an_unchanged_path_is_dropped() -> None:
    findings, verdict = run(finding(rubric_id="ARCH-03", path="backend/src/app/main.py"))
    assert findings == ()
    assert verdict is ReviewVerdict.APPROVED


def test_unknown_changed_paths_keep_every_finding() -> None:
    findings, _ = run(finding(rubric_id="ARCH-03", path="anything.py"), changed=None)
    assert len(findings) == 1


def test_duplicates_merge_and_keep_the_blocker() -> None:
    nit = finding(ReviewSeverity.NIT, rubric_id="ARCH-03", evidence="Minor.")
    block = finding(rubric_id="ARCH-03", evidence="Serious.")
    other_line = finding(rubric_id="ARCH-03", line=40)
    findings, _ = run(nit, block, other_line)
    assert len(findings) == 2
    assert findings[0].evidence == "Serious."


def test_blockers_come_first_and_at_most_ten_are_kept() -> None:
    nits = [finding(ReviewSeverity.NIT, line=index) for index in range(1, 12)]
    late_blocker = finding(rubric_id="SEC-06", line=99)
    findings, _ = run(*nits, late_blocker)
    assert len(findings) == MAX_FINDINGS
    assert findings[0].line == 99


@pytest.mark.parametrize(
    ("review_round", "verdict"),
    [(1, ReviewVerdict.CHANGES_REQUESTED), (2, ReviewVerdict.NEEDS_DECISION)],
)
def test_blockers_request_changes_once_then_need_a_decision(
    review_round: int, verdict: ReviewVerdict
) -> None:
    assert run(finding(rubric_id="ARCH-03"), review_round=review_round)[1] is verdict


def test_no_findings_is_approved_in_any_round() -> None:
    assert run(review_round=2)[1] is ReviewVerdict.APPROVED


def test_text_is_cut_to_the_spec_caps_and_bad_lines_cleared() -> None:
    long = finding(rubric_id="ARCH-03", evidence="e" * 900, fix="f" * 900, line=0)
    cleaned = run(long)[0][0]
    assert len(cleaned.evidence) == 600
    assert cleaned.fix is not None
    assert len(cleaned.fix) == 600
    assert cleaned.line is None


def test_diff_paths_reads_both_sides_of_a_unified_diff() -> None:
    diff = (
        "diff --git a/old.py b/new.py\n--- a/old.py\n+++ b/new.py\n"
        "diff --git a/gone.py b/gone.py\n--- a/gone.py\n+++ /dev/null\n"
        "diff --git a/added.py b/added.py\n--- /dev/null\n+++ b/added.py\n"
    )
    assert diff_paths(diff) == {"old.py", "new.py", "gone.py", "added.py"}
