"""Row <-> record mapping for `task_reviews` (findings are a JSONB list of objects)."""

from __future__ import annotations

from app.adapters.db.devboard_mappers import fields
from app.adapters.db.tables_board import TaskReview
from app.domain.devboard.model import ReviewFinding, ReviewRecord, ReviewSeverity, ReviewVerdict


def review_values(review: ReviewRecord) -> dict[str, object]:
    return {
        "id": review.id,
        "task_id": review.task_id,
        "run_id": review.run_id,
        "round": review.round,
        "verdict": review.verdict.value,
        "summary": review.summary,
        "findings": [finding_value(finding) for finding in review.findings],
        "model": review.model,
        "created_at": review.created_at,
    }


def review_record(row: TaskReview) -> ReviewRecord:
    items = (fields(item) for item in row.findings)
    return ReviewRecord(
        id=row.id,
        task_id=row.task_id,
        run_id=row.run_id,
        round=row.round,
        verdict=ReviewVerdict(row.verdict),
        summary=row.summary,
        findings=tuple(finding_record(item) for item in items if item is not None),
        created_at=row.created_at,
        model=row.model,
    )


def finding_value(finding: ReviewFinding) -> dict[str, object]:
    return {
        "severity": finding.severity.value,
        "path": finding.path,
        "evidence": finding.evidence,
        "rubric_id": finding.rubric_id,
        "test_id": finding.test_id,
        "repro": finding.repro,
        "line": finding.line,
        "fix": finding.fix,
        "downgraded": finding.downgraded,
    }


def finding_record(item: dict[str, object]) -> ReviewFinding:
    line = item.get("line")
    return ReviewFinding(
        severity=ReviewSeverity(str(item["severity"])),
        path=str(item.get("path") or ""),
        evidence=str(item.get("evidence") or ""),
        rubric_id=_optional(item.get("rubric_id")),
        test_id=_optional(item.get("test_id")),
        repro=_optional(item.get("repro")),
        line=line if isinstance(line, int) else None,
        fix=_optional(item.get("fix")),
        downgraded=item.get("downgraded") is True,
    )


def _optional(value: object) -> str | None:
    return None if value is None else str(value)
