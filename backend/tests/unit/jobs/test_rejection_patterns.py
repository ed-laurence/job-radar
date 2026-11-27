"""Rejection patterns: counts across rejections, most common first, latest distinct lessons."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.domain.jobs.records import RejectionRecord
from app.domain.jobs.rejections import rejection_patterns
from app.domain.jobs.roles import RoleDraft, StatusChange
from tests.unit.jobs.support import ALICE, BOB, Domain

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def _rejection(*categories: str, lesson: str | None = None, days: int = 0) -> RejectionRecord:
    return RejectionRecord(
        id=uuid.uuid4(),
        role_id=uuid.uuid4(),
        version=1,
        rejected_at=NOW - timedelta(days=days),
        reason_categories=categories,
        summary="Said no." if categories else None,
        lesson=lesson,
    )


def test_counts_order_and_lessons() -> None:
    rows = [
        _rejection("pay", "skills_gap", lesson="State your rate early."),
        _rejection("skills_gap", lesson="Show Spark work.", days=1),
        _rejection("skills_gap", lesson="State your rate early.", days=2),
        _rejection(days=3),
    ]
    patterns = rejection_patterns(rows)
    assert (patterns.total, patterns.analysed) == (4, 3)
    assert patterns.categories == (("skills_gap", 3), ("pay", 1))
    assert patterns.lessons == ("State your rate early.", "Show Spark work.")


async def test_patterns_are_per_user_and_skip_deleted_roles() -> None:
    domain = Domain()
    for title in ("Data Engineer", "Analyst"):
        role = await domain.roles.create_role(ALICE, RoleDraft(title=title))
        await domain.roles.change_status(ALICE, role.id, StatusChange(status="rejected", version=1))
    await domain.roles.delete_role(ALICE, role.id, 2)
    assert (await domain.rejections.patterns(ALICE)).total == 1
    assert (await domain.rejections.patterns(BOB)).total == 0
