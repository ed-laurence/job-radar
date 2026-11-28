"""`task_reviews` and the card's branch fields in Postgres, under the app role (FORCE RLS)."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.devboard_runs import PgRunStore
from app.adapters.db.devboard_tasks import PgTaskStore
from app.domain.devboard.model import (
    Column,
    ReviewFinding,
    ReviewRecord,
    ReviewSeverity,
    ReviewVerdict,
    RunKind,
    RunRecord,
    RunStatus,
    TaskRecord,
)
from tests.integration.devboard.cleanup import leave_no_rows

pytestmark = pytest.mark.integration
__all__ = ["leave_no_rows"]  # the autouse fixture

type Sessions = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 11, 28, 1, 0, tzinfo=UTC)
BASE = "c" * 40


def _review(task_id: uuid.UUID, run_id: uuid.UUID, review_round: int) -> ReviewRecord:
    findings = (
        ReviewFinding(
            ReviewSeverity.BLOCKER,
            path="backend/src/app/api/x.py",
            evidence="A rule in the route.",
            rubric_id="ARCH-03",
            test_id="tests/unit/x.py::test_y",
            repro="POST twice.",
            line=12,
            fix="Move it to the domain.",
        ),
        ReviewFinding(ReviewSeverity.NIT, path="", evidence="Human note.", downgraded=True),
    )
    return ReviewRecord(
        id=uuid.uuid4(),
        task_id=task_id,
        run_id=run_id,
        round=review_round,
        verdict=ReviewVerdict.NEEDS_DECISION,
        summary="Checked the change.",
        findings=findings,
        created_at=NOW + timedelta(minutes=review_round),
        model="claude-opus",
    )


async def test_reviews_and_branch_fields_round_trip(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    maintainer, user = two_users
    tasks = PgTaskStore(sessions)
    runs = PgRunStore(sessions)
    task = TaskRecord(uuid.uuid4(), "Card", "Body", Column.BACKLOG, "U", 1, user, NOW, NOW)
    await tasks.insert(user, task)
    assert await tasks.latest_review(maintainer, task.id) is None

    loaded = await tasks.get(maintainer, task.id)
    assert loaded is not None
    assert (loaded.branch, loaded.base_sha, loaded.review_round) == (None, None, 0)
    changed = replace(loaded, branch="agent/0000aaaa-1111bbbb", base_sha=BASE, review_round=2)
    saved = await tasks.save(maintainer, changed, loaded.version)
    assert (saved.branch, saved.base_sha, saved.review_round) == (
        "agent/0000aaaa-1111bbbb",
        BASE,
        2,
    )

    run = RunRecord(uuid.uuid4(), task.id, RunKind.REVIEW, RunStatus.QUEUED, NOW, agent="reviewer")
    await runs.insert(maintainer, run)
    first, second = _review(task.id, run.id, 1), _review(task.id, run.id, 2)
    await tasks.add_review(maintainer, first)
    await tasks.add_review(maintainer, second)
    latest = await tasks.latest_review(user, task.id)
    assert latest == second
