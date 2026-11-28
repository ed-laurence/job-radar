"""The strict `/out/review.json` parser: the sandboxed reviewer's output is untrusted, so unknown
keys, bad severities, over-cap text, too many findings, another task's answer and links are
refused. Citations are the domain's call (`review_policy`), not this parser's."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.adapters.agents.review_file import (
    MAX_REVIEW_BYTES,
    ReviewFileError,
    parse_review,
    read_review,
)
from app.domain.devboard.model import ReviewFinding, ReviewSeverity
from tests.unit.agents_runner.fakes import TASK_ID


def finding(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "severity": "BLOCKER",
        "rubric_id": "ARCH-03",
        "test_id": None,
        "repro": None,
        "path": "backend/src/app/api/tasks.py",
        "line": 12,
        "evidence": "The route decides the column itself.",
        "fix": "Move the rule into the board service.",
    }
    return body | changes


def review_file(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "task_id": str(TASK_ID),
        "summary": "One blocker in the route; tests cover the rest.",
        "findings": [finding(), finding(severity="NIT", rubric_id=None, line=None, fix=None)],
    }
    return body | changes


def _parse(payload: object) -> object:
    return parse_review(json.dumps(payload).encode(), str(TASK_ID))


def test_a_valid_review_becomes_the_draft() -> None:
    draft = parse_review(json.dumps(review_file()).encode(), str(TASK_ID))
    assert draft.summary == "One blocker in the route; tests cover the rest."
    assert draft.findings[0] == ReviewFinding(
        severity=ReviewSeverity.BLOCKER,
        path="backend/src/app/api/tasks.py",
        evidence="The route decides the column itself.",
        rubric_id="ARCH-03",
        line=12,
        fix="Move the rule into the board service.",
    )
    assert draft.findings[1].severity is ReviewSeverity.NIT
    assert draft.findings[1].line is None


def test_no_findings_is_a_valid_review() -> None:
    draft = parse_review(json.dumps(review_file(findings=[])).encode(), str(TASK_ID))
    assert draft.findings == ()


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(review_file(extra=1), id="unknown-key"),
        pytest.param(review_file(findings=[finding(extra=1)]), id="unknown-finding-key"),
        pytest.param(review_file(findings=[finding(severity="MAJOR")]), id="bad-severity"),
        pytest.param(review_file(findings=[finding(line=0)]), id="line-zero"),
        pytest.param(review_file(findings=[finding(line="12")]), id="line-not-int"),
        pytest.param(review_file(findings=[finding()] * 11), id="too-many-findings"),
        pytest.param(review_file(summary=" ".join(["word"] * 81)), id="summary-words"),
        pytest.param(review_file(findings=[finding(evidence="x " * 61)]), id="evidence-words"),
        pytest.param(review_file(findings=[finding(fix="x " * 61)]), id="fix-words"),
        pytest.param(review_file(findings=[finding(repro="x " * 61)]), id="repro-words"),
        pytest.param(review_file(findings=[finding(evidence="")]), id="empty-evidence"),
        pytest.param(review_file(findings=[finding(path="/etc/passwd")]), id="absolute-path"),
        pytest.param(review_file(findings=[finding(path="a/../b.py")]), id="dotdot-path"),
        pytest.param(review_file(findings=[finding(rubric_id="arch 03")]), id="bad-rubric-id"),
        pytest.param({k: v for k, v in review_file().items() if k != "summary"}, id="missing"),
        pytest.param([review_file()], id="not-an-object"),
    ],
)
def test_malformed_reviews_are_refused(payload: object) -> None:
    with pytest.raises(ReviewFileError, match="malformed"):
        _parse(payload)


def test_another_tasks_review_is_refused() -> None:
    with pytest.raises(ReviewFileError, match="another task"):
        _parse(review_file(task_id="someone-else"))


def test_oversized_and_non_json_files_are_refused() -> None:
    with pytest.raises(ReviewFileError, match="too large"):
        parse_review(b" " * (MAX_REVIEW_BYTES + 1), str(TASK_ID))
    with pytest.raises(ReviewFileError, match="malformed"):
        parse_review(b"{not json", str(TASK_ID))


def _always(_self: Path) -> bool:
    return True


def test_read_review_refuses_a_missing_file_and_a_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "review.json"
    with pytest.raises(ReviewFileError, match="no review"):
        read_review(path, str(TASK_ID))
    path.write_text(json.dumps(review_file()), encoding="utf-8")
    monkeypatch.setattr(Path, "is_symlink", _always)
    with pytest.raises(ReviewFileError, match="regular file"):
        read_review(path, str(TASK_ID))


def test_read_review_reads_a_regular_file(tmp_path: Path) -> None:
    path = tmp_path / "review.json"
    path.write_text(json.dumps(review_file()), encoding="utf-8")
    assert len(read_review(path, str(TASK_ID)).findings) == 2
