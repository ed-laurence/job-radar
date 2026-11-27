"""Health panel domain: job schedules, LLM spend and site usage over hand-built facts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.adapters.fakes.health import MemoryHealth
from app.adapters.fakes.system import FakeClock
from app.domain.devboard.schedule import JobName
from app.domain.health import SCHEDULES, HealthService, job_health, llm_health
from app.domain.identity import AppRole, Principal
from app.domain.ports.health import JobRunFact, UsageCallFact

NOW = datetime(2026, 11, 25, 20, 7, tzinfo=UTC)  # a Wednesday
USER_ID = uuid.UUID(int=7)


def _utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


def test_next_runs_follow_the_schedules() -> None:
    assert SCHEDULES[JobName.SCAN].next_after(NOW) == _utc(2026, 11, 25, 20, 15)
    assert SCHEDULES[JobName.WATCHDOG].next_after(NOW) == _utc(2026, 11, 25, 20, 15)
    assert SCHEDULES[JobName.DIGEST].next_after(NOW) == _utc(2026, 11, 26, 20, 0)
    # Rollup is nightly (it snapshots cards per column for the dev-board charts).
    assert SCHEDULES[JobName.ROLLUP].next_after(NOW) == _utc(2026, 11, 25, 21, 0)
    on_tick = _utc(2026, 11, 25, 21, 0)
    assert SCHEDULES[JobName.ROLLUP].next_after(on_tick) == _utc(2026, 11, 26, 21, 0)


def test_job_status_is_ok_failed_or_never() -> None:
    runs = [
        JobRunFact(job="scan", started_at=NOW - timedelta(hours=1), ok=True),
        JobRunFact(job="digest", started_at=NOW - timedelta(days=1), ok=False),
    ]
    jobs = {job.name: job for job in job_health(runs, NOW)}
    assert list(jobs) == ["scan", "digest", "rollup", "watchdog"]
    assert jobs["scan"].last_status == "ok"
    assert jobs["scan"].last_run_at == NOW - timedelta(hours=1)
    assert jobs["digest"].last_status == "failed"
    assert jobs["rollup"].last_status == "never"
    assert jobs["rollup"].last_run_at is None
    assert all(job.next_run_at is not None and job.next_run_at > NOW for job in jobs.values())


def _call(
    at: datetime, cost: float, *, feature: str = "score", fallback: bool = False
) -> UsageCallFact:
    return UsageCallFact(
        at=at, feature=feature, provider="vertex-claude", cost_usd=cost, fallback_used=fallback
    )


def test_llm_spend_today_month_and_fallback_rate() -> None:
    calls = [
        _call(NOW - timedelta(hours=1), 0.5),
        _call(NOW - timedelta(days=2), 0.25, feature="kit", fallback=True),
        _call(NOW - timedelta(days=20), 1.0),
        _call(NOW - timedelta(days=40), 9.0),  # last month
    ]
    llm = llm_health(calls, NOW, 50.0, usd_to_aud=2.0)  # money is AUD: USD cost x the rate
    assert llm.spend_today_aud == 1.0
    assert llm.calls_today == 1
    assert llm.spend_month_aud == 3.5
    assert llm.budget_month_aud == 50.0  # already AUD: not converted
    assert llm.fallback_rate_7d == 0.5
    assert [(r.feature, r.calls, r.spend_aud) for r in llm.by_feature] == [
        ("score", 2, 3.0),
        ("kit", 1, 0.5),
    ]
    assert llm_health(calls, NOW, None).spend_today_aud == 0.775  # the default rate, 1.55


async def test_empty_health_is_zeros_nulls_and_never() -> None:
    principal = Principal(user_id=USER_ID, email="m@example.test", role=AppRole.MAINTAINER)
    view = await HealthService(MemoryHealth(), FakeClock(NOW)).summary(principal)
    assert {job.last_status for job in view.jobs} == {"never"}
    assert view.llm.spend_today_aud == 0
    assert view.llm.fallback_rate_7d is None
    assert view.llm.budget_month_aud is None
    assert view.llm.by_feature == ()
    assert view.site.page_views_7d == 0
    assert view.site.web_vitals_p75 == ()
