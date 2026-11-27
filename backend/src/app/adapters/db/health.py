"""Postgres adapter for the health panel. `job_runs` and `web_events` are system tables; LLM
calls are read under the caller's row-level security."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.engine import system_transaction, user_transaction
from app.domain.ports.health import JobRunFact, SiteUsage, UsageCallFact

_LAST_RUNS = text(
    "SELECT DISTINCT ON (job) job, started_at, ok, error_code FROM job_runs"
    " WHERE job IN :jobs AND finished_at IS NOT NULL AND ok IS NOT NULL"
    " ORDER BY job, started_at DESC"
).bindparams(bindparam("jobs", expanding=True))
_CALLS = text(
    "SELECT at, feature, provider, cost_usd, fallback_used FROM llm_calls WHERE at >= :start"
)
_VIEWS = text(
    "SELECT count(*), count(DISTINCT session_id) FROM web_events"
    " WHERE type = 'page_view' AND day >= :day AND at >= :start"
)
_VITALS = text(
    "SELECT metric, percentile_cont(0.75) WITHIN GROUP (ORDER BY value) FROM web_events"
    " WHERE type = 'web_vital' AND metric IS NOT NULL AND value IS NOT NULL"
    " AND day >= :day AND at >= :start GROUP BY metric ORDER BY metric"
)


class PgHealth:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def last_job_runs(self, jobs: tuple[str, ...]) -> list[JobRunFact]:
        async with system_transaction(self._sessions) as session:
            rows = (await session.execute(_LAST_RUNS, {"jobs": list(jobs)})).all()
        return [
            JobRunFact(job=str(r[0]), started_at=r[1], ok=bool(r[2]), error_code=r[3]) for r in rows
        ]

    async def llm_calls(self, user_id: uuid.UUID, start: datetime) -> list[UsageCallFact]:
        async with user_transaction(self._sessions, user_id) as session:
            rows = (await session.execute(_CALLS, {"start": start})).all()
        return [
            UsageCallFact(
                at=r[0],
                feature=str(r[1]),
                provider=str(r[2]),
                cost_usd=float(r[3]),
                fallback_used=bool(r[4]),
            )
            for r in rows
        ]

    async def site_usage(self, start: datetime) -> SiteUsage:
        values = {"day": start.date(), "start": start}
        async with system_transaction(self._sessions) as session:
            views = (await session.execute(_VIEWS, values)).one()
            vitals = (await session.execute(_VITALS, values)).all()
        return SiteUsage(
            page_views=int(views[0]),
            sessions=int(views[1]),
            vitals_p75=tuple((str(r[0]), float(r[1])) for r in vitals),
        )
