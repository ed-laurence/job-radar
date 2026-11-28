"""The ``/out/review.json`` contract: strict keys, at most 10 findings, word caps, and every
finding's path is a file the branch changed (computed with git in the sandbox)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import review_contract as rc

CHANGED = frozenset({"web/src/features/board/Card.tsx", "web/src/features/board/Card.test.tsx"})


def finding(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "severity": "BLOCKER",
        "rubric_id": "TEST-01",
        "path": "web/src/features/board/Card.tsx",
        "line": 12,
        "evidence": "The badge is rendered without a test.",
        "fix": "Add a test for the badge.",
    }
    return body | changes


def review(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "summary": "One blocker: the badge has no test.",
        "findings": [finding(), finding(severity="NIT", rubric_id=None, line=None, fix=None)],
    }
    return body | changes


def test_a_valid_review_is_filled_to_the_full_contract() -> None:
    out = rc.validate(review(), "T-1", CHANGED)
    assert list(out) == list(rc.TOP_KEYS)
    assert out["task_id"] == "T-1"
    findings = json.loads(json.dumps(out["findings"]))
    assert [list(item) for item in findings] == [list(rc.FINDING_KEYS)] * 2
    assert findings[1]["test_id"] is None
    assert findings[1]["repro"] is None


def test_no_findings_is_a_valid_review() -> None:
    assert rc.validate(review(findings=[]), "T-1", CHANGED)["findings"] == []


@pytest.mark.parametrize(
    ("raw", "needle"),
    [
        (review(extra=1), "unknown key"),
        (review(findings=[finding(extra=1)]), "unknown key"),
        (review(findings=[finding(severity="MAJOR")]), "BLOCKER, NIT"),
        (review(findings=[finding()] * 11), "at most 10"),
        (review(summary=" ".join(["w"] * 81)), "80 words"),
        (review(summary=""), "summary"),
        (review(findings=[finding(evidence="w " * 61)]), "60 words"),
        (review(findings=[finding(fix="w " * 61)]), "60 words"),
        (review(findings=[finding(repro="w " * 61)]), "60 words"),
        (review(findings=[finding(evidence=None)]), "evidence"),
        (review(findings=[finding(path="web/src/other.tsx")]), "not a file this branch changed"),
        (review(findings=[finding(path="/etc/passwd")]), "repo-relative"),
        (review(findings=[finding(line=0)]), "line"),
        (review(findings=[finding(line=True)]), "line"),
        (review(findings=[finding(rubric_id="arch 3")]), "rubric_id"),
        (review(task_id="T-2"), "task_id"),
        (review(findings="none"), "findings"),
    ],
)
def test_contract_violations_are_listed(raw: dict[str, object], needle: str) -> None:
    with pytest.raises(rc.ReviewError) as caught:
        rc.validate(raw, "T-1", CHANGED)
    assert needle in "; ".join(caught.value.errors)


def test_a_non_object_is_refused() -> None:
    with pytest.raises(rc.ReviewError):
        rc.validate([review()], "T-1", CHANGED)


def test_the_summary_line_is_counts_only() -> None:
    out = rc.validate(review(), "T-1", CHANGED)
    assert rc.summary(out) == "2 findings (1 blocker, 1 nit)"


def test_write_is_atomic_json(tmp_path: Path) -> None:
    out = rc.validate(review(), "T-1", CHANGED)
    path = tmp_path / "out" / "review.json"
    rc.write(out, path)
    assert json.loads(path.read_text(encoding="utf-8")) == out
    assert not path.with_name("review.json.tmp").exists()


def test_changed_files_are_read_from_the_nul_separated_git_listing(tmp_path: Path) -> None:
    listing = tmp_path / "changed"
    listing.write_bytes(b"a.py" + bytes(1) + b"web/src/b c.tsx" + bytes(1) + b"gone.py" + bytes(1))
    assert rc.read_changed(listing) == frozenset({"a.py", "web/src/b c.tsx", "gone.py"})
