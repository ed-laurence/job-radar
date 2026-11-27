"""The campaign plan and the main page's counts over the seeded data under RLS, equal to
independent SQL run as the database owner. Read-only: the seed is shared by the session."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, time, timedelta
from typing import Any, cast

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.campaign_plan import YamlPlanSource
from app.adapters.db.jobs_campaign import PgCampaign
from app.adapters.db.jobs_roles import PgRoles
from app.adapters.fakes.security import FakeContentGuard
from app.adapters.fakes.system import FakeClock, SequentialIds
from app.domain.identity import AppRole, Principal
from app.domain.jobs.campaign import CampaignService
from app.domain.jobs.roles import RoleService
from tests.integration.jobs.conftest import Seeded

pytestmark = pytest.mark.integration

type Sessions = async_sessionmaker[AsyncSession]
CLOCK = FakeClock()


def _service(sessions: Sessions, seeded: Seeded) -> CampaignService:
    ids = SequentialIds()
    roles = RoleService(PgRoles(sessions), FakeContentGuard(), CLOCK, ids)
    plan = YamlPlanSource(seeded.settings.campaign_config)
    return CampaignService(plan, PgCampaign(sessions), roles, CLOCK, ids)


async def _value(owner: asyncpg.Connection, sql: str, *args: object) -> Any:
    rows = await _rows(owner, sql, *args)
    return next(iter(rows[0].values())) if rows else None


async def _rows(owner: asyncpg.Connection, sql: str, *args: object) -> list[Mapping[str, Any]]:
    return cast("list[Mapping[str, Any]]", await owner.fetch(sql, *args))


def _me(seeded: Seeded) -> Principal:
    return Principal(
        user_id=seeded.user_id, email="seed-owner@example.test", role=AppRole.MAINTAINER
    )


async def test_plan_lines_up_with_the_seed_and_counts_match_sql(
    sessions: Sessions, seeded: Seeded, owner: asyncpg.Connection
) -> None:
    service = _service(sessions, seeded)
    anchor = await _value(
        owner,
        "SELECT notes FROM campaign_notes WHERE user_id = $1 AND key = 'seed_anchor'",
        seeded.user_id,
    )
    assert anchor is not None
    view = await service.plan(_me(seeded), None)
    assert view.week_start.weekday() == 0
    assert view.current_week == (CLOCK.now().date() - view.start).days // 7 + 1
    assert view.current_week == 6

    for week in range(1, view.total_weeks + 1):
        selected = await service.plan(_me(seeded), week)
        first, end = selected.week_start, selected.week_end + timedelta(days=1)
        low = datetime.combine(first, time.min, tzinfo=UTC)
        high = datetime.combine(end, time.min, tzinfo=UTC)
        invites = await _value(
            owner,
            "SELECT count(*) FROM campaign_activity WHERE user_id = $1 AND kind = 'linkedin_invite'"
            " AND on_date >= $2 AND on_date < $3",
            seeded.user_id,
            first,
            end,
        )
        applied = await _value(
            owner,
            "SELECT count(*) FROM roles WHERE user_id = $1 AND deleted_at IS NULL"
            " AND applied_at >= $2 AND applied_at < $3",
            seeded.user_id,
            low,
            high,
        )
        actual = {m.key: m.actual for m in selected.metrics}
        assert actual["invites"] == invites, week
        assert actual["applications"] == applied, week


async def test_seeded_done_marks_and_activity_are_per_user(
    sessions: Sessions, seeded: Seeded, owner: asyncpg.Connection
) -> None:
    service = _service(sessions, seeded)
    done: set[tuple[str, int]] = {
        (str(r["task_id"]), int(r["week"]))
        for r in await _rows(
            owner, "SELECT task_id, week FROM campaign_task_done WHERE user_id = $1", seeded.user_id
        )
    }
    assert done
    for week in {w for _, w in done}:
        tasks = await service.tasks(_me(seeded), week)
        marked = {(t.id, week) for t in tasks.items if t.done}
        assert marked == {(task, w) for task, w in done if w == week}
    stranger = Principal(user_id=uuid.uuid4(), email="x@example.test", role=AppRole.USER)
    assert (await service.activity(stranger, None)).items == ()
    assert not any(t.done for t in (await service.tasks(stranger, 1)).items)


async def test_main_page_counts_match_sql(
    sessions: Sessions, seeded: Seeded, owner: asyncpg.Connection
) -> None:
    before = CLOCK.now() - timedelta(days=1)
    facts = await PgCampaign(sessions).main_page(seeded.user_id, before)
    outreach = await _value(
        owner,
        "SELECT count(*) FROM contacts WHERE user_id = $1 AND deleted_at IS NULL"
        " AND status IN ('new', 'drafted')",
        seeded.user_id,
    )
    feedback = await _value(
        owner,
        "SELECT count(*) FROM rejections j JOIN roles r ON r.id = j.role_id"
        " WHERE j.user_id = $1 AND r.deleted_at IS NULL AND j.summary IS NULL"
        " AND cardinality(j.reason_categories) = 0",
        seeded.user_id,
    )
    due = await _value(
        owner,
        "SELECT count(*) FROM roles WHERE user_id = $1 AND deleted_at IS NULL"
        " AND status = 'applied' AND followed_up_at IS NULL AND applied_at <= $2",
        seeded.user_id,
        before,
    )
    assert (facts.outreach_due, facts.feedback_due, facts.followups_due) == (
        outreach,
        feedback,
        due,
    )
    rows = await PgCampaign(sessions).rejection_summaries(seeded.user_id, 0, 500)
    total = await _value(
        owner,
        "SELECT count(*) FROM rejections j JOIN roles r ON r.id = j.role_id"
        " WHERE j.user_id = $1 AND r.deleted_at IS NULL",
        seeded.user_id,
    )
    assert len(rows) == total
    assert all(row.title for row in rows)
