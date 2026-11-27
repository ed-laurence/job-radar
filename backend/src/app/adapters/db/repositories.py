"""Postgres adapters for the foundation ports: LLM usage, rate limits and web events."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.engine import system_transaction, user_transaction
from app.domain.ports import (
    Clock,
    LlmCallRecord,
    LlmFeature,
    PageViews,
    StoredWebEvent,
    VitalSample,
)
from app.domain.rate_limits import RateDecision, RateTier, TierPolicy, decide

type Sessions = async_sessionmaker[AsyncSession]


class PgLlmUsageStore:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def record_call(self, record: LlmCallRecord) -> None:
        # llm_calls is user-owned, so the insert runs under that user's RLS scope.
        async with user_transaction(self._sessions, record.user_id) as session:
            await session.execute(
                text(
                    "INSERT INTO llm_calls (user_id, at, feature, provider, model, input_tokens,"
                    " cache_read_tokens, output_tokens, cost_usd, latency_ms, ok, error_code,"
                    " fallback_used, guard_input, guard_output, role_id, run_id, research_run_id)"
                    " VALUES (:user_id, :at, :feature, :provider, :model, :input_tokens,"
                    " :cache_read_tokens, :output_tokens, :cost_usd, :latency_ms, :ok, :error_code,"
                    " :fallback_used, :guard_input, :guard_output, :role_id, :run_id,"
                    " :research_run_id)"
                ),
                {
                    "user_id": record.user_id,
                    "at": record.at,
                    "feature": record.feature.value,
                    "provider": record.provider,
                    "model": record.model,
                    "input_tokens": record.input_tokens,
                    "cache_read_tokens": record.cache_read_tokens,
                    "output_tokens": record.output_tokens,
                    "cost_usd": Decimal(str(round(record.cost_usd, 6))),
                    "latency_ms": record.latency_ms,
                    "ok": record.is_ok,
                    "error_code": record.error_code,
                    "fallback_used": record.is_fallback,
                    "guard_input": record.guard_input,
                    "guard_output": record.guard_output,
                    "role_id": record.role_id,
                    "run_id": record.run_id,
                    "research_run_id": record.research_run_id,
                },
            )

    async def spent(self, day: date, feature: LlmFeature) -> float:
        async with system_transaction(self._sessions) as session:
            value = await session.scalar(
                text("SELECT cost_usd FROM llm_budget_usage WHERE day = :day AND feature = :f"),
                {"day": day, "f": feature.value},
            )
        return float(value or 0)

    async def add_spend(self, day: date, feature: LlmFeature, cost_usd: float) -> None:
        async with system_transaction(self._sessions) as session:
            await session.execute(
                text(
                    "INSERT INTO llm_budget_usage (day, feature, cost_usd) VALUES (:day, :f, :c)"
                    " ON CONFLICT (day, feature) DO UPDATE SET"
                    " cost_usd = llm_budget_usage.cost_usd + EXCLUDED.cost_usd, updated_at = now()"
                ),
                {"day": day, "f": feature.value, "c": Decimal(str(round(cost_usd, 6)))},
            )


# One statement per request: lock the bucket, refill, take a token, write it back, and return
# the balance before taking so `decide` (shared with the in-memory fake) makes the call.
_CONSUME = text(
    """
    WITH current AS (
        SELECT tokens, refilled_at FROM rate_limit_buckets
        WHERE key = :key AND tier = :tier FOR UPDATE
    ), available AS (
        SELECT LEAST(
            CAST(:capacity AS numeric),
            COALESCE(current.tokens, :capacity) + GREATEST(
                0, EXTRACT(EPOCH FROM (CAST(:now AS timestamptz) - current.refilled_at))
            ) * :rate
        ) AS tokens
        FROM (SELECT 1) AS one LEFT JOIN current ON true
    ), written AS (
        INSERT INTO rate_limit_buckets (key, tier, tokens, refilled_at)
        SELECT :key, :tier,
               CASE WHEN tokens >= 1 THEN tokens - 1 ELSE tokens END, CAST(:now AS timestamptz)
        FROM available
        ON CONFLICT (key, tier) DO UPDATE
            SET tokens = EXCLUDED.tokens, refilled_at = EXCLUDED.refilled_at
        RETURNING 1
    )
    SELECT available.tokens FROM available, written
    """
)


class PgRateLimiter:
    def __init__(self, sessions: Sessions, clock: Clock) -> None:
        self._sessions = sessions
        self._clock = clock

    async def consume(self, key: str, tier: RateTier, policy: TierPolicy) -> RateDecision:
        async with system_transaction(self._sessions) as session:
            available = await session.scalar(
                _CONSUME,
                {
                    "key": key,
                    "tier": tier.value,
                    "capacity": policy.capacity,
                    "rate": Decimal(str(policy.refill_per_second)),
                    "now": self._clock.now(),
                },
            )
        _, decision = decide(float(available or 0), policy)
        return decision


class PgWebEvents:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def insert_many(self, events: Sequence[StoredWebEvent]) -> int:
        if not events:
            return 0
        rows = [
            {
                "at": event.at,
                "day": event.day,
                "session_id": event.session_id,
                "role_tag": event.role_tag,
                "type": event.type,
                "route": event.route,
                "name": event.name,
                "metric": event.metric,
                "value": event.value,
                "rating": event.rating,
            }
            for event in events
        ]
        async with system_transaction(self._sessions) as session:
            await session.execute(
                text(
                    "INSERT INTO web_events (at, day, session_id, role_tag, type, route, name,"
                    " metric, value, rating) VALUES (:at, :day, :session_id, :role_tag, :type,"
                    " :route, :name, :metric, :value, :rating)"
                ),
                rows,
            )
        return len(rows)

    async def page_views(self, start: date, end: date) -> list[PageViews]:
        async with system_transaction(self._sessions) as session:
            result = await session.execute(
                text(
                    "SELECT route, count(*), count(DISTINCT session_id) FROM web_events"
                    " WHERE type = 'page_view' AND day >= :start AND day < :end GROUP BY route"
                ),
                {"start": start, "end": end},
            )
            return [PageViews(row[0], int(row[1]), int(row[2])) for row in result]

    async def product_event_sessions(self, start: date, end: date) -> list[tuple[str, str]]:
        async with system_transaction(self._sessions) as session:
            result = await session.execute(
                text(
                    "SELECT DISTINCT session_id, name FROM web_events WHERE type = 'product'"
                    " AND name IS NOT NULL AND day >= :start AND day < :end ORDER BY 1, 2"
                ),
                {"start": start, "end": end},
            )
            return [(str(row[0]), str(row[1])) for row in result]

    async def vitals_p75(self, start: date, end: date) -> list[VitalSample]:
        async with system_transaction(self._sessions) as session:
            result = await session.execute(
                text(
                    "SELECT route, metric, percentile_cont(0.75) WITHIN GROUP (ORDER BY value),"
                    " count(*) FROM web_events WHERE type = 'web_vital' AND value IS NOT NULL"
                    " AND day >= :start AND day < :end GROUP BY route, metric ORDER BY 1, 2"
                ),
                {"start": start, "end": end},
            )
            return [VitalSample(row[0], row[1], float(row[2]), int(row[3])) for row in result]
