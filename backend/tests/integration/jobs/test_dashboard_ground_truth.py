"""Every dashboard KPI and series, computed by the service over the seeded data under RLS, equals
an independent SQL query run as the database owner."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from datetime import date
from typing import Any, cast

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.jobs_dashboard import PgDashboard
from app.adapters.fakes.system import FakeClock
from app.domain.identity import AppRole, Principal
from app.domain.jobs.dashboard import ROLE_STATUSES, TASK_COLUMNS, DashboardService
from app.domain.jobs.metrics import DateRange
from tests.integration.conftest import Database
from tests.integration.jobs.board_data import insert_board
from tests.integration.jobs.conftest import Seeded

pytestmark = pytest.mark.integration

RATE = 1.6  # USD_TO_AUD for these tests; ground truth is summed in USD


async def _rows(owner: asyncpg.Connection, sql: str, *args: object) -> list[dict[str, Any]]:
    """asyncpg records are untyped; ground-truth values are compared loosely by name."""
    records = cast("list[Mapping[str, Any]]", await owner.fetch(sql, *args))
    return [dict(record) for record in records]


async def _row(owner: asyncpg.Connection, sql: str, *args: object) -> dict[str, Any] | None:
    rows = await _rows(owner, sql, *args)
    return rows[0] if rows else None


type Sessions = async_sessionmaker[AsyncSession]
WINDOW = DateRange(date(2026, 9, 1), date(2026, 12, 1), "week")
ARGS = (WINDOW.start_at, WINDOW.end_at)
OPEN = "('new', 'reviewing', 'applied', 'interview', 'offer')"


@pytest.fixture(scope="module", autouse=True)
def board(database: Database) -> uuid.UUID:
    """The seed has no dev-board data, so these tests insert their own."""
    return asyncio.run(insert_board(database.owner_url))


@pytest.fixture
def service(sessions: Sessions) -> DashboardService:
    return DashboardService(PgDashboard(sessions), FakeClock(), usd_to_aud=RATE)


@pytest.fixture
def me(seeded: Seeded) -> Principal:
    return Principal(
        user_id=seeded.user_id, email="seed-owner@example.test", role=AppRole.MAINTAINER
    )


def _near(actual: float | None, expected: Any, places: int = 2) -> bool:
    if expected is None or actual is None:
        return actual is None and expected is None
    return abs(actual - float(expected)) <= 10**-places


async def test_headline_kpis(
    service: DashboardService, me: Principal, owner: asyncpg.Connection
) -> None:
    kpis = await service.kpis(me, WINDOW)
    truth = await _row(
        owner,
        f"""
        WITH applied AS (
            SELECT * FROM roles WHERE user_id = $1 AND deleted_at IS NULL
              AND applied_at >= $2 AND applied_at < $3
        )
        SELECT
          (SELECT count(*) FROM roles WHERE user_id = $1 AND deleted_at IS NULL AND status IN {OPEN}) AS open_roles,
          (SELECT count(*) FROM applied) AS applied,
          (SELECT count(*) FROM applied a WHERE a.status IN ('interview', 'offer') OR EXISTS (
              SELECT 1 FROM role_events e WHERE e.role_id = a.id AND e.kind = 'status'
                AND e.to_value IN ('interview', 'offer') AND e.at >= $2)) AS interviewed,
          (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM applied_at - posted_at) / 3600)
             FROM applied WHERE posted_at IS NOT NULL AND applied_at >= posted_at) AS median_hours,
          (SELECT coalesce(sum(cost_usd), 0) FROM llm_calls WHERE user_id = $1 AND at >= $2 AND at < $3) AS spend,
          (SELECT count(*) FROM runs WHERE kind = 'review' AND round = 0 AND finished_at >= $2 AND finished_at < $3) AS reviews,
          (SELECT count(*) FROM runs WHERE kind = 'review' AND round = 0 AND outcome = 'approved'
             AND finished_at >= $2 AND finished_at < $3) AS passed,
          (SELECT count(*) FROM dev_tasks WHERE deleted_at IS NULL
             AND "column" NOT IN ('backlog', 'out_of_scope', 'done')) AS in_flight
        """,
        me.user_id,
        *ARGS,
    )
    assert truth is not None
    assert kpis.open_roles == truth["open_roles"] > 0
    assert kpis.applied_in_range == truth["applied"] > 0
    expected_rate = truth["interviewed"] / truth["applied"]
    assert _near(kpis.interview_rate, expected_rate, 4)
    assert _near(kpis.median_post_to_apply_hours, truth["median_hours"])
    assert _near(kpis.llm_spend_aud, float(truth["spend"]) * RATE, 5)  # SQL truth is USD
    assert kpis.llm_spend_aud > 0
    assert _near(kpis.agent_pass_rate, truth["passed"] / truth["reviews"], 4)
    assert kpis.tasks_in_flight == truth["in_flight"] > 0


async def test_funnel(service: DashboardService, me: Principal, owner: asyncpg.Connection) -> None:
    rows = await _rows(
        owner,
        """
        SELECT status, count(DISTINCT role_id) AS n FROM (
            SELECT to_value AS status, role_id FROM role_events
             WHERE user_id = $1 AND kind = 'status' AND to_value IS NOT NULL AND at >= $2 AND at < $3
            UNION ALL
            SELECT 'new', id FROM roles
             WHERE user_id = $1 AND deleted_at IS NULL AND found_at >= $2 AND found_at < $3
        ) reached GROUP BY status
        """,
        me.user_id,
        *ARGS,
    )
    truth = {row["status"]: row["n"] for row in rows}
    funnel = {item.key: item.count for item in await service.funnel(me, WINDOW)}
    assert funnel == {status: truth.get(status, 0) for status in ROLE_STATUSES}
    assert funnel["passed"] > 0


async def test_post_to_apply(
    service: DashboardService, me: Principal, owner: asyncpg.Connection
) -> None:
    rows = await _rows(
        owner,
        """
        SELECT date_trunc('week', applied_at AT TIME ZONE 'UTC')::date AS period, count(*) AS n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM applied_at - posted_at) / 3600) AS median
          FROM roles WHERE user_id = $1 AND deleted_at IS NULL AND applied_at >= $2 AND applied_at < $3
           AND posted_at IS NOT NULL AND applied_at >= posted_at
         GROUP BY 1 ORDER BY 1
        """,
        me.user_id,
        *ARGS,
    )
    view = await service.post_to_apply(me, WINDOW)
    assert [(i.period_start, i.count) for i in view.items] == [(r["period"], r["n"]) for r in rows]
    assert all(_near(i.median_hours, r["median"]) for i, r in zip(view.items, rows, strict=True))
    overall = await _row(
        owner,
        """
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY h) AS p50,
               percentile_cont(0.75) WITHIN GROUP (ORDER BY h) AS p75
          FROM (SELECT extract(epoch FROM applied_at - posted_at) / 3600 AS h FROM roles
                 WHERE user_id = $1 AND deleted_at IS NULL AND applied_at >= $2 AND applied_at < $3
                   AND posted_at IS NOT NULL AND applied_at >= posted_at) hours
        """,
        me.user_id,
        *ARGS,
    )
    assert overall is not None
    assert _near(view.median_hours, overall["p50"])
    assert _near(view.p75_hours, overall["p75"])


async def test_score_distribution(
    service: DashboardService, me: Principal, owner: asyncpg.Connection
) -> None:
    rows = await _rows(
        owner,
        """
        SELECT least(score / 10, 9) AS bin, count(*) AS n FROM roles
         WHERE user_id = $1 AND deleted_at IS NULL AND score IS NOT NULL
           AND found_at >= $2 AND found_at < $3 GROUP BY 1
        """,
        me.user_id,
        *ARGS,
    )
    truth = {row["bin"]: row["n"] for row in rows}
    bins = await service.score_distribution(me, WINDOW)
    assert [b.count for b in bins] == [truth.get(index, 0) for index in range(10)]
    assert sum(b.count for b in bins) > 100  # 300 visible seed roles (the rest are pooled)


async def test_rejection_reasons(
    service: DashboardService, me: Principal, owner: asyncpg.Connection
) -> None:
    rows = await _rows(
        owner,
        """
        SELECT category, count(*) AS n FROM rejections, unnest(reason_categories) AS category
         WHERE user_id = $1 AND rejected_at >= $2 AND rejected_at < $3
         GROUP BY 1
        """,
        me.user_id,
        *ARGS,
    )
    reasons = {item.key: item.count for item in await service.rejection_reasons(me, WINDOW)}
    assert reasons == {row["category"]: row["n"] for row in rows}
    assert sum(reasons.values()) == 13


async def test_llm_spend_by_feature_and_provider(
    service: DashboardService, me: Principal, owner: asyncpg.Connection
) -> None:
    rows = await _rows(
        owner,
        """
        SELECT date_trunc('week', at AT TIME ZONE 'UTC')::date AS period, feature, provider,
               count(*) AS calls, sum(input_tokens) AS input, sum(output_tokens) AS output,
               sum(cost_usd) AS cost
          FROM llm_calls WHERE user_id = $1 AND at >= $2 AND at < $3
         GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
        """,
        me.user_id,
        *ARGS,
    )
    spend = await service.llm_spend(me, WINDOW)
    assert [
        (s.period_start, s.feature, s.provider, s.calls, s.input_tokens, s.output_tokens)
        for s in spend
    ] == [
        (r["period"], r["feature"], r["provider"], r["calls"], r["input"], r["output"])
        for r in rows
    ]
    assert all(
        _near(s.cost_aud, float(r["cost"]) * RATE, 5) for s, r in zip(spend, rows, strict=True)
    )


async def test_dev_cycle_time_per_column(
    service: DashboardService, me: Principal, owner: asyncpg.Connection
) -> None:
    rows = await _rows(
        owner,
        """
        WITH moves AS (
            SELECT e.to_column AS col, e.at,
                   lead(e.at) OVER (PARTITION BY e.task_id ORDER BY e.at) AS left_at
              FROM task_events e JOIN dev_tasks t ON t.id = e.task_id
             WHERE e.to_column IS NOT NULL AND t.deleted_at IS NULL AND e.at < $2
        )
        SELECT col, count(*) AS n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM left_at - at) / 3600) AS p50,
               percentile_cont(0.75) WITHIN GROUP (ORDER BY extract(epoch FROM left_at - at) / 3600) AS p75
          FROM moves WHERE left_at >= $1 AND left_at < $2 GROUP BY col
        """,
        *ARGS,
    )
    truth = {row["col"]: row for row in rows}
    cycle = await service.cycle_time(me, WINDOW)
    assert [c.column for c in cycle] == list(TASK_COLUMNS)
    for item in cycle:
        row = truth.get(item.column)
        assert item.samples == (row["n"] if row else 0), item.column
        assert _near(item.median_hours, row["p50"] if row else None), item.column
        assert _near(item.p75_hours, row["p75"] if row else None), item.column
    assert sum(item.samples for item in cycle) > 0


async def test_agent_pass_rate_by_mode(
    service: DashboardService, me: Principal, owner: asyncpg.Connection
) -> None:
    rows = await _rows(
        owner,
        """
        SELECT date_trunc('week', r.finished_at AT TIME ZONE 'UTC')::date AS period,
               coalesce((SELECT p.mode FROM task_plans p WHERE p.task_id = r.task_id
                          ORDER BY p.created_at DESC LIMIT 1), 'single') AS mode,
               count(*) AS runs, count(*) FILTER (WHERE r.outcome = 'approved') AS passed
          FROM runs r WHERE r.kind = 'review' AND r.round = 0
           AND r.finished_at >= $1 AND r.finished_at < $2
         GROUP BY 1, 2 ORDER BY 1, 2
        """,
        *ARGS,
    )
    series = await service.agent_pass_rate(me, WINDOW)
    assert [(s.period_start, s.mode, s.runs, s.passed) for s in series] == [
        (r["period"], r["mode"], r["runs"], r["passed"]) for r in rows
    ]
    assert {s.mode for s in series} == {"single", "multi"}
