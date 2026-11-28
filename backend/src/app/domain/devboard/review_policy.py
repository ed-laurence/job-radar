"""Code's rules over a reviewer's answer (docs/features/agent-review.md section 2).

- A BLOCKER needs a citation: a known rubric id, a `test_id` or a `repro`. Without one it is
  kept as a NIT with `downgraded=True`, so the human still sees it.
- A finding on a path the branch didn't change is dropped, not downgraded: the reviewer may
  only judge the change, and a finding about other code is noise in the Changes panel.
  `changed_paths=None` means the changed files are unknown (no published branch, e.g. the
  offline simulation), and then no finding is dropped for its path.
- A `rubric_id` that is not in the rubric is cleared (it can't be a citation).
- Duplicates (same path, line and rubric id) are merged into the first, keeping the most
  severe; then BLOCKERs come first and at most `MAX_FINDINGS` are kept.
- Verdict: no blockers -> `approved`; blockers in round 1 -> `changes_requested`; blockers
  from round 2 on -> `needs_decision` (a human decides; no third automatic round).
"""

from __future__ import annotations

import re
from dataclasses import replace

from app.domain.devboard.model import ReviewFinding, ReviewSeverity, ReviewVerdict
from app.domain.ports.devboard import ReviewDraft

MAX_FINDINGS = 10
# The spec's caps on `ReviewFinding` / `TaskReview` text.
MAX_TEXT = 600
MAX_ID = 300
MAX_SUMMARY = 1000
_DIFF_SIDE = re.compile(r"^(?:---|\+\+\+) [ab]/(.+?)\t?$", re.MULTILINE)

type FindingKey = tuple[str, int | None, str | None]


def finalise(
    draft: ReviewDraft,
    *,
    review_round: int,
    rubric_ids: frozenset[str],
    changed_paths: frozenset[str] | None,
) -> tuple[tuple[ReviewFinding, ...], ReviewVerdict]:
    """The stored findings and the verdict for review round `review_round` (1-based)."""
    kept = [
        _cited(_clean(finding, rubric_ids))
        for finding in draft.findings
        if changed_paths is None or finding.path in changed_paths
    ]
    merged = _merge(kept)
    ordered = sorted(merged, key=lambda f: f.severity is not ReviewSeverity.BLOCKER)
    findings = tuple(ordered[:MAX_FINDINGS])
    return findings, verdict_for(findings, review_round=review_round)


def verdict_for(findings: tuple[ReviewFinding, ...], *, review_round: int) -> ReviewVerdict:
    if not any(f.severity is ReviewSeverity.BLOCKER for f in findings):
        return ReviewVerdict.APPROVED
    return ReviewVerdict.CHANGES_REQUESTED if review_round <= 1 else ReviewVerdict.NEEDS_DECISION


def clean_summary(summary: str) -> str:
    return summary.strip()[:MAX_SUMMARY]


def diff_paths(diff: str) -> frozenset[str]:
    """Every path on either side of a unified diff (a rename names both, a delete the old)."""
    return frozenset(match.group(1) for match in _DIFF_SIDE.finditer(diff))


def _clean(finding: ReviewFinding, rubric_ids: frozenset[str]) -> ReviewFinding:
    return replace(
        finding,
        rubric_id=finding.rubric_id if finding.rubric_id in rubric_ids else None,
        test_id=_text(finding.test_id, MAX_ID),
        repro=_text(finding.repro, MAX_TEXT),
        path=finding.path[:MAX_ID],
        line=finding.line if finding.line is not None and finding.line >= 1 else None,
        evidence=finding.evidence.strip()[:MAX_TEXT],
        fix=_text(finding.fix, MAX_TEXT),
    )


def _cited(finding: ReviewFinding) -> ReviewFinding:
    if finding.severity is not ReviewSeverity.BLOCKER:
        return finding
    if finding.rubric_id or finding.test_id or finding.repro:
        return finding
    return replace(finding, severity=ReviewSeverity.NIT, downgraded=True)


def _merge(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    merged: dict[FindingKey, ReviewFinding] = {}
    for finding in findings:
        key = (finding.path, finding.line, finding.rubric_id)
        first = merged.get(key)
        if first is None or _outranks(finding, first):
            merged[key] = finding
    return list(merged.values())


def _outranks(finding: ReviewFinding, other: ReviewFinding) -> bool:
    return finding.severity is ReviewSeverity.BLOCKER and other.severity is ReviewSeverity.NIT


def _text(value: str | None, limit: int) -> str | None:
    clean = (value or "").strip()[:limit]
    return clean or None
