"""Job summary, dev-board metrics and the health panel, computed over the seeded data under RLS,
equal independent SQL queries run as the database owner. Empty cases return nulls, not errors."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator, Mapping
from datetime import date, timedelta
from typing import Any, cast

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.health import PgHealth
from app.adapters.db.jobs_dashboard import PgDashboard
from app.adapters.fakes.system import FakeClock
from app.domain.health import HealthService
from app.domain.identity import AppRole, Principal
from app.domain.jobs.dashboard import DashboardService
from app.domain.jobs.metrics import DateRange
from tests.integration.conftest import Database
from tests.integration.jobs.board_data import insert_board
from tests.integration.jobs.conftest import Seeded

pytestmark = pytest.mark.integration

type Sessions = async_sessionmaker[AsyncSession]
WINDOW = DateRange(date(2026, 9, 1), date(2026, 12, 1), "week")
ARGS = (WINDOW.start_at, WINDOW.end_at)
CLOCK = FakeClock()
NOW = CLOCK.now()


async def _row(owner: asyncpg.Connection, sql: str, *args: object) -> dict[str, Any]:
    records = cast("list[Mapping[str, Any]]", await owner.fetch(sql, *args))
    assert records
    return dict(records[0])


async def _rows(owner: asyncpg.Connection, sql: str, *args: object) -> list[dict[str, Any]]:
    records = cast("list[Mapping[str, Any]]", await owner.fetch(sql, *args))
    return [dict(record) for record in records]


def _near(actual: float | None, expected: Any, places: int = 2) -> bool:
    if expected is None or actual is None:
        return actual is None and expected is None
    return abs(actual - float(expected)) <= 10**-places


@pytest.fixture(scope="module", autouse=True)
def board(database: Database) -> uuid.UUID:
    """The seed has no dev-board data, so these tests insert their own."""
    return asyncio.run(insert_board(database.owner_url))


@pytest.fixture
def service(sessions: Sessions) -> DashboardService:
    return DashboardService(PgDashboard(sessions), CLOCK)


@pytest.fixture
def me(seeded: Seeded) -> Principal:
    return Principal(
        user_id=seeded.user_id, email="seed-owner@example.test", role=AppRole.MAINTAINER
    )


LIVE = "user_id = $1 AND deleted_at IS NULL"
ACT = "status IN ('new', 'reviewing') AND stage IS DISTINCT FROM 'filtered'"


async def test_job_summary(
    service: DashboardService, me: Principal, owner: asyncpg.Connection
) -> None:
    view = await service.job_summary(me)
    truth = await _row(
        owner,
        f"""
        SELECT
          (SELECT count(*) FROM roles WHERE {LIVE} AND {ACT}) AS act_now,
          (SELECT max(extract(epoch FROM $2 - coalesce(posted_at, found_at)) / 3600)
             FROM roles WHERE {LIVE} AND {ACT}) AS oldest,
          (SELECT count(*) FROM roles WHERE {LIVE} AND applied_at >= $2 - interval '7 days'
             AND applied_at <= $2) AS recent,
          (SELECT percentile_cont(0.5) WITHIN GROUP
                    (ORDER BY extract(epoch FROM applied_at - posted_at) / 3600)
             FROM roles WHERE {LIVE} AND posted_at IS NOT NULL AND applied_at >= posted_at
          ) AS median,
          (SELECT count(*) FROM roles WHERE {LIVE} AND status IN ('new', 'reviewing')
             AND stage = 'filtered') AS filtered
        """,
        me.user_id,
        NOW,
    )
    tabs = await _rows(
        owner, f"SELECT status, count(*) AS n FROM roles WHERE {LIVE} GROUP BY status", me.user_id
    )
    by_status = {row["status"]: row["n"] for row in tabs}
    assert view.to_act_on == truth["act_now"] == view.tab_counts["act_now"]
    assert view.to_act_on > 0
    assert _near(view.oldest_waiting_hours, max(0.0, float(truth["oldest"])))
    assert view.applied_last_7_days == truth["recent"]
    assert _near(view.median_post_to_apply_hours, truth["median"])
    assert view.tab_counts["filtered"] == truth["filtered"]
    assert view.tab_counts["applied"] == by_status.get("applied", 0)
    interviewing = by_status.get("interview", 0) + by_status.get("offer", 0)
    assert view.tab_counts["interviewing"] == interviewing
    assert view.tab_counts["passed"] == by_status.get("passed", 0) + by_status.get("closed", 0)
    assert view.tab_counts["rejected"] == by_status.get("rejected", 0)
    assert view.tab_counts["unknown"] == by_status.get("unknown", 0)
    assert sum(view.tab_counts.values()) == sum(by_status.values())


async def test_job_summary_for_a_user_with_no_roles(service: DashboardService) -> None:
    nobody = Principal(user_id=uuid.uuid4(), email="nobody@example.test", role=AppRole.USER)
    view = await service.job_summary(nobody)
    assert view.to_act_on == 0
    assert view.oldest_waiting_hours is None
    assert view.median_post_to_apply_hours is None
    assert set(view.tab_counts.values()) == {0}


DONE = """
    SELECT e.task_id, min(e.at) AS done_at FROM task_events e
      JOIN dev_tasks t ON t.id = e.task_id AND t.deleted_at IS NULL
     WHERE e.to_column = 'done' AND e.at >= $1 AND e.at < $2 GROUP BY e.task_id
"""


async def test_dev_metrics_flow(
    service: DashboardService, me: Principal, owner: asyncpg.Connection
) -> None:
    view = await service.dev_metrics(me, WINDOW)
    weeks = await _rows(
        owner,
        """
        SELECT date_trunc('week', e.at AT TIME ZONE 'UTC')::date AS week,
               count(DISTINCT e.task_id) AS n
          FROM task_events e JOIN dev_tasks t ON t.id = e.task_id AND t.deleted_at IS NULL
         WHERE e.to_column = 'done' AND e.at >= $1 AND e.at < $2 GROUP BY 1
        """,
        *ARGS,
    )
    throughput = dict(view.throughput)
    assert {w: n for w, n in throughput.items() if n} == {r["week"]: r["n"] for r in weeks}
    assert sum(throughput.values()) > 0
    wip = await _rows(
        owner,
        """SELECT "column", count(*) AS n FROM dev_tasks WHERE deleted_at IS NULL
            AND "column" NOT IN ('backlog', 'out_of_scope', 'done') GROUP BY 1""",
    )
    assert {c: n for c, n in view.wip_by_column if n} == {r["column"]: r["n"] for r in wip}
    lead = await _row(
        owner,
        f"""
        WITH done AS ({DONE}),
        hours AS (SELECT greatest(0, extract(epoch FROM d.done_at - t.created_at) / 3600) AS h
                    FROM done d JOIN dev_tasks t ON t.id = d.task_id)
        SELECT (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY h) FROM hours) AS p50,
               (SELECT percentile_cont(0.9) WITHIN GROUP (ORDER BY h) FROM hours) AS p90,
               (SELECT coalesce(sum(cost_usd), 0) FROM runs
                 WHERE task_id IN (SELECT task_id FROM done)) AS cost,
               (SELECT count(*) FROM done) AS n
        """,
        *ARGS,
    )
    assert lead["n"] > 0
    assert _near(view.lead_time_hours_p50, lead["p50"])
    assert _near(view.lead_time_hours_p90, lead["p90"])
    assert _near(view.avg_cost_per_task_usd, float(lead["cost"]) / lead["n"], 4)
    blocked = await _row(
        owner,
        """SELECT count(*) AS n FROM dev_tasks WHERE deleted_at IS NULL AND "column" = 'blocked'""",
    )
    assert view.blocked_count == blocked["n"]


AGENTS = """
WITH reviews AS (SELECT * FROM runs WHERE kind = 'review' AND finished_at >= $1 AND finished_at < $2),
triage AS (SELECT DISTINCT ON (task_id) task_id, in_scope FROM task_triage
            WHERE created_at >= $1 AND created_at < $2 ORDER BY task_id, created_at DESC)
SELECT
  (SELECT count(*) FILTER (WHERE outcome = 'approved')::float / nullif(count(*), 0)
     FROM reviews WHERE round = 0) AS pass_rate,
  (SELECT avg(rounds) FROM (SELECT max(round) + 1 AS rounds FROM reviews GROUP BY task_id) t)
    AS rounds,
  (SELECT count(*) FILTER (WHERE NOT in_scope)::float / nullif(count(*), 0) FROM triage)
    AS out_of_scope,
  (SELECT max(extract(epoch FROM $3 - coalesce(
      (SELECT max(e.at) FROM task_events e WHERE e.task_id = t.id
         AND e.to_column = 'human_review' AND e.at <= $3), t.created_at)) / 3600)
     FROM dev_tasks t WHERE t.deleted_at IS NULL AND t."column" = 'human_review') AS oldest
"""

CYCLE = """
WITH moves AS (
    SELECT e.to_column AS col, e.at,
           lead(e.at) OVER (PARTITION BY e.task_id ORDER BY e.at) AS next_at
      FROM task_events e JOIN dev_tasks t ON t.id = e.task_id AND t.deleted_at IS NULL
     WHERE e.to_column IS NOT NULL AND e.at < $2)
SELECT col, percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM next_at - at) / 3600)
       AS p50
  FROM moves WHERE next_at >= $1 AND next_at < $2 GROUP BY col
"""


async def test_dev_metrics_agents(
    service: DashboardService, me: Principal, owner: asyncpg.Connection
) -> None:
    view = await service.dev_metrics(me, WINDOW)
    truth = await _row(owner, AGENTS, *ARGS, NOW)
    assert _near(view.agent_pass_rate, truth["pass_rate"], 4)
    assert _near(view.avg_review_rounds, truth["rounds"], 4)
    assert _near(view.out_of_scope_rate, truth["out_of_scope"], 4)
    oldest = None if truth["oldest"] is None else max(0.0, float(truth["oldest"]))
    assert _near(view.oldest_human_review_hours, oldest)
    outcomes = await _rows(
        owner,
        """SELECT CASE status WHEN 'timed_out' THEN 'failed' ELSE status END AS outcome,
                  count(*) AS n FROM runs WHERE created_at >= $1 AND created_at < $2 GROUP BY 1""",
        *ARGS,
    )
    assert {o: n for o, n in view.run_outcomes if n} == {r["outcome"]: r["n"] for r in outcomes}
    truth_cycle = {row["col"]: row["p50"] for row in await _rows(owner, CYCLE, *ARGS)}
    assert set(dict(view.cycle_time_by_column)) == set(truth_cycle)
    assert all(_near(h, truth_cycle[c]) for c, h in view.cycle_time_by_column)


async def test_dev_metrics_for_an_empty_range(service: DashboardService, me: Principal) -> None:
    view = await service.dev_metrics(me, DateRange(date(2020, 1, 6), date(2020, 1, 20), "week"))
    assert [n for _, n in view.throughput] == [0, 0]
    assert view.lead_time_hours_p50 is None
    assert view.agent_pass_rate is None
    assert view.avg_review_rounds is None
    assert view.out_of_scope_rate is None
    assert view.avg_cost_per_task_usd is None
    assert view.cycle_time_by_column == ()


SESSION = "it-health-session-01"
INSERT_EVENT = (
    "INSERT INTO web_events (day, at, session_id, role_tag, type, route, metric, value)"
    " VALUES (($1::timestamptz AT TIME ZONE 'UTC')::date, $1, $2, 'maintainer', $3,"
    " '/it-health', $4, $5)"
)


@pytest.fixture
async def health_rows(owner: asyncpg.Connection) -> AsyncGenerator[None]:
    """Job runs and web events for the health panel (system tables), removed afterwards."""
    await owner.execute(
        "INSERT INTO job_runs (job, started_at, finished_at, ok) VALUES"
        " ('digest', $1::timestamptz - interval '5 hours', $1::timestamptz - interval '4 hours', true),"
        " ('digest', $1::timestamptz - interval '2 hours', $1::timestamptz - interval '1 hour', false),"
        " ('watchdog', $1::timestamptz - interval '10 minutes', NULL, NULL)",
        NOW,
    )
    rows: list[tuple[str, str | None, float | None]] = [("page_view", None, None)] * 2
    rows += [("web_vital", "LCP", value) for value in (1200.0, 2600.0, 3100.0, 1900.0)]
    for kind, metric, value in rows:
        await owner.execute(INSERT_EVENT, NOW - timedelta(hours=3), SESSION, kind, metric, value)
    try:
        yield
    finally:
        await owner.execute("DELETE FROM job_runs WHERE job IN ('digest', 'watchdog')")
        await owner.execute("DELETE FROM web_events WHERE session_id = $1", SESSION)


LLM = """
SELECT
  (SELECT coalesce(sum(cost_usd), 0) FROM llm_calls WHERE user_id = $1
     AND at >= date_trunc('day', $2, 'UTC') AND at <= $2) AS today,
  (SELECT count(*) FROM llm_calls WHERE user_id = $1
     AND at >= date_trunc('day', $2, 'UTC') AND at <= $2) AS calls,
  (SELECT coalesce(sum(cost_usd), 0) FROM llm_calls WHERE user_id = $1
     AND at >= date_trunc('month', $2, 'UTC') AND at <= $2) AS month,
  (SELECT count(*) FILTER (WHERE fallback_used)::float / nullif(count(*), 0) FROM llm_calls
    WHERE user_id = $1 AND at >= $2::timestamptz - interval '7 days' AND at <= $2) AS fallback
"""

SITE = """
SELECT count(*) FILTER (WHERE type = 'page_view') AS views,
       count(DISTINCT session_id) FILTER (WHERE type = 'page_view') AS sessions,
       percentile_cont(0.75) WITHIN GROUP (ORDER BY value)
         FILTER (WHERE type = 'web_vital' AND metric = 'LCP') AS lcp
  FROM web_events WHERE at >= $1::timestamptz - interval '7 days'
"""


@pytest.mark.usefixtures("health_rows")
async def test_health_summary(sessions: Sessions, me: Principal, owner: asyncpg.Connection) -> None:
    rate = 1.6  # SQL ground truth is USD; the panel shows AUD at the deployment's rate
    view = await HealthService(PgHealth(sessions), CLOCK, usd_to_aud=rate).summary(me)
    jobs = {job.name: job for job in view.jobs}
    assert jobs["digest"].last_status == "failed"
    assert jobs["digest"].last_run_at == NOW - timedelta(hours=2)
    assert jobs["watchdog"].last_status == "never"  # its only run has not finished
    assert jobs["scan"].next_run_at is not None
    llm = await _row(owner, LLM, me.user_id, NOW)
    assert _near(view.llm.spend_today_aud, float(llm["today"]) * rate, 5)
    assert view.llm.calls_today == llm["calls"]
    assert _near(view.llm.spend_month_aud, float(llm["month"]) * rate, 5)
    assert _near(view.llm.fallback_rate_7d, llm["fallback"], 4)
    assert _near(sum(row.spend_aud for row in view.llm.by_feature), float(llm["month"]) * rate, 4)
    assert view.llm.budget_month_aud is None
    site = await _row(owner, SITE, NOW)
    assert view.site.page_views_7d == site["views"]
    assert view.site.page_views_7d >= 2
    assert view.site.sessions_7d == site["sessions"]
    lcp = next(v for v in view.site.web_vitals_p75 if v.metric == "LCP")
    assert _near(lcp.value, site["lcp"], 4)
    assert lcp.budget == 2500.0


async def test_health_summary_with_no_calls_or_events(sessions: Sessions) -> None:
    nobody = Principal(user_id=uuid.uuid4(), email="nobody@example.test", role=AppRole.MAINTAINER)
    view = await HealthService(PgHealth(sessions), FakeClock(NOW + timedelta(days=3650))).summary(
        nobody
    )
    assert len(view.jobs) == 4
    assert view.llm.calls_today == 0
    assert view.llm.spend_month_aud == 0
    assert view.llm.fallback_rate_7d is None
    assert view.llm.by_feature == ()
    assert view.site.page_views_7d == 0
    assert view.site.web_vitals_p75 == ()
