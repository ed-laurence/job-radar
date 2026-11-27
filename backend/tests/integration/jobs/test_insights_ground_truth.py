"""Job insights over the seeded data under RLS equal an independent SQL query run as the database
owner; a user with no data gets nulls. Also the Postgres settings store: versions, 409 and RLS."""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Mapping
from datetime import date
from typing import Any, cast

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.jobs_dashboard import PgDashboard
from app.adapters.db.user_settings import PgUserSettings
from app.adapters.fakes.system import FakeClock
from app.adapters.fakes.user_settings import MemoryUserSettings
from app.domain.errors import Conflict
from app.domain.identity import AppRole, Principal
from app.domain.jobs.dashboard import DashboardService
from app.domain.jobs.metrics import DateRange
from app.domain.user_settings import DEFAULTS, FollowupPrefs, SettingsService
from app.jobs.seed_settings import owner_skills
from tests.integration.jobs.conftest import Seeded

pytestmark = pytest.mark.integration

type Sessions = async_sessionmaker[AsyncSession]
WINDOW = DateRange(date(2026, 9, 1), date(2026, 12, 1), "week")
CLOCK = FakeClock()

BASE = """
WITH live AS (SELECT * FROM roles WHERE user_id = $1 AND deleted_at IS NULL),
reached AS (
    SELECT l.id, array_agg(DISTINCT x.s) AS st FROM live l
    CROSS JOIN LATERAL (
        SELECT e.to_value AS s FROM role_events e
         WHERE e.role_id = l.id AND e.kind = 'status' AND e.to_value IS NOT NULL
        UNION SELECT l.status
    ) x GROUP BY l.id
),
facts AS (SELECT l.*, r.st FROM live l JOIN reached r ON r.id = l.id),
applied AS (SELECT * FROM facts WHERE applied_at >= $2 AND applied_at < $3),
found AS (SELECT * FROM facts WHERE found_at >= $2 AND found_at < $3)
"""


async def _rows(owner: asyncpg.Connection, sql: str, *args: object) -> list[dict[str, Any]]:
    records = cast("list[Mapping[str, Any]]", await owner.fetch(sql, *args))
    return [dict(record) for record in records]


def _near(actual: float | None, expected: Any, places: int) -> bool:
    if expected is None or actual is None:
        return actual is None and expected is None
    return abs(actual - float(expected)) <= 10**-places


def _rate(part: Any, whole: Any) -> float | None:
    return part / whole if whole else None


@pytest.fixture
def me(seeded: Seeded) -> Principal:
    return Principal(
        user_id=seeded.user_id, email="seed-owner@example.test", role=AppRole.MAINTAINER
    )


@pytest.mark.parametrize("due_hours", [48, 24])
async def test_insights_match_sql(
    sessions: Sessions, me: Principal, owner: asyncpg.Connection, due_hours: int
) -> None:
    prefs = SettingsService(MemoryUserSettings(CLOCK))
    wanted = dataclasses.replace(
        DEFAULTS, followups=FollowupPrefs(after_hours=min(24, due_hours), due_hours=due_hours)
    )
    await prefs.update(me, wanted)
    service = DashboardService(PgDashboard(sessions), CLOCK, prefs=prefs)
    view = await service.insights(me, WINDOW)
    args = (me.user_id, WINDOW.start_at, WINDOW.end_at)
    [truth] = await _rows(
        owner,
        BASE
        + """
        SELECT count(*) AS n,
          count(*) FILTER (WHERE st && ARRAY['interview', 'offer', 'rejected']) AS responded,
          count(*) FILTER (WHERE st && ARRAY['interview', 'offer']) AS interviewed,
          count(*) FILTER (WHERE 'offer' = ANY(st)) AS offers,
          count(*) FILTER (WHERE status = 'unknown') AS ghosted,
          count(*) FILTER (WHERE followed_up_at IS NOT NULL
                             OR applied_at + make_interval(hours => $5) <= $4) AS due,
          count(*) FILTER (WHERE followed_up_at IS NOT NULL
                             AND followed_up_at <= applied_at + make_interval(hours => $5)) AS on_time,
          (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY d) FROM (
              SELECT extract(epoch FROM min(e.at) - a.applied_at) / 86400 AS d
                FROM applied a JOIN role_events e ON e.role_id = a.id AND e.kind = 'status'
                 AND e.to_value IN ('interview', 'offer', 'rejected') AND e.at >= a.applied_at
               GROUP BY a.id, a.applied_at) t) AS median_days
        FROM applied
        """,
        *args,
        CLOCK.now(),
        due_hours,
    )
    assert truth["n"] > 0
    assert truth["responded"] > 0
    assert truth["due"] > 0
    assert _near(view.response_rate, _rate(truth["responded"], truth["n"]), 4)
    assert _near(view.interview_rate, _rate(truth["interviewed"], truth["n"]), 4)
    assert _near(view.offer_rate, _rate(truth["offers"], truth["n"]), 4)
    assert _near(view.ghost_rate, _rate(truth["ghosted"], truth["n"]), 4)
    assert _near(view.followup_on_time_rate, _rate(truth["on_time"], truth["due"]), 4)
    assert _near(view.median_days_to_response, truth["median_days"], 2)

    weeks = await _rows(
        owner,
        BASE
        + """SELECT date_trunc('week', applied_at AT TIME ZONE 'UTC')::date AS week, count(*) AS n
               FROM applied GROUP BY 1 ORDER BY 1""",
        *args,
    )
    assert [(w.week_start, w.applied) for w in view.applications_per_week] == [
        (row["week"], row["n"]) for row in weeks
    ][-60:]

    breakdown = """
        SELECT {key} AS key, count(*) AS found, count(applied_at) AS applied,
               count(*) FILTER (WHERE st && ARRAY['interview', 'offer']) AS interviews,
               count(*) FILTER (WHERE 'rejected' = ANY(st)) AS rejected
          FROM found GROUP BY 1 ORDER BY found DESC, ({key}) COLLATE "C" LIMIT 20
    """

    def rows_of(items: Any) -> list[tuple[Any, ...]]:
        return [(b.key, b.found, b.applied, b.interviews, b.rejected) for b in items]

    def truth_of(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
        keys = ("key", "found", "applied", "interviews", "rejected")
        return [tuple(row[k] for k in keys) for row in rows]

    sources = await _rows(owner, BASE + breakdown.format(key="left(source, 40)"), *args)
    assert sources  # every seeded role has source `seed`
    assert rows_of(view.by_source) == truth_of(sources)
    tracks = await _rows(owner, BASE + breakdown.format(key="coalesce(track, 'unknown')"), *args)
    assert len(tracks) > 1
    assert rows_of(view.by_track) == truth_of(tracks)
    band = """CASE WHEN score IS NULL THEN 'unscored' WHEN score < 45 THEN '0-44'
                   WHEN score < 55 THEN '45-54' WHEN score < 75 THEN '55-74' ELSE '75-100' END"""
    bands = {
        row["key"]: row for row in await _rows(owner, BASE + breakdown.format(key=band), *args)
    }
    order = ["0-44", "45-54", "55-74", "75-100"] + (["unscored"] if "unscored" in bands else [])
    empty = {"found": 0, "applied": 0, "interviews": 0, "rejected": 0}
    expected = [(k, *(bands.get(k, {"key": k, **empty})[f] for f in list(empty))) for k in order]
    assert rows_of(view.score_bands) == expected
    assert sum(b.found for b in view.score_bands) > 0


async def test_insights_for_a_user_with_no_data_are_null(sessions: Sessions) -> None:
    nobody = Principal(user_id=uuid.uuid4(), email="nobody@example.test", role=AppRole.USER)
    view = await DashboardService(PgDashboard(sessions), CLOCK).insights(nobody, WINDOW)
    assert view.applications_per_week == ()
    assert view.response_rate is None
    assert view.interview_rate is None
    assert view.offer_rate is None
    assert view.ghost_rate is None
    assert view.median_days_to_response is None
    assert view.followup_on_time_rate is None
    assert view.by_source == view.by_track == ()
    assert [(b.key, b.found) for b in view.score_bands] == [
        ("0-44", 0),
        ("45-54", 0),
        ("55-74", 0),
        ("75-100", 0),
    ]


async def test_settings_store_versions_conflicts_and_rls(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    store = PgUserSettings(sessions)
    alice, bob = two_users
    assert await store.load(alice) is None
    first = await store.save(alice, {"archive_default_days": 9}, 1)
    assert first.version == 2
    assert first.updated_at is not None
    with pytest.raises(Conflict):
        await store.save(alice, {"archive_default_days": 10}, 1)
    second = await store.save(alice, {"archive_default_days": 11}, 2)
    assert second.version == 3
    loaded = await store.load(alice)
    assert loaded is not None
    assert loaded.data == {"archive_default_days": 11}
    # Bob can't see Alice's row, and his first write is his own.
    assert await store.load(bob) is None
    with pytest.raises(Conflict):
        await store.save(bob, {}, 2)
    assert (await store.save(bob, {"archive_default_days": 3}, 1)).version == 2
    reloaded = await store.load(alice)
    assert reloaded is not None
    assert reloaded.data == {"archive_default_days": 11}


async def test_settings_keep_the_seeded_profile_keys(sessions: Sessions, seeded: Seeded) -> None:
    service = SettingsService(PgUserSettings(sessions))
    owner = Principal(user_id=seeded.user_id, email="seed-owner@example.test", role=AppRole.USER)
    current = await service.get(owner)
    # The seed fills the owner's extra skills (`seed/owner-settings.json`), not the brief.
    assert (current.skills, current.brief) == (owner_skills(seeded.settings.seed_dir), "")
    saved = await service.update(owner, dataclasses.replace(current, skills="SQL"))
    assert saved.skills == "SQL"
    stored = await PgUserSettings(sessions).load(seeded.user_id)
    assert stored is not None
    profile = cast("dict[str, object]", stored.data["profile"])
    assert profile["name"] == "Alex Example"
    restored = await service.update(owner, dataclasses.replace(saved, skills=""))
    assert restored.skills == ""
