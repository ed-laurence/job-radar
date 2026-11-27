"""Dev-board facts for the dashboard ground-truth tests. The seed has no board data, so these
tests insert their own: six cards across the columns, single and multi plans, first-round
reviews that pass and fail, triage in and out of scope. Idempotent (fixed ids, `ON CONFLICT`)."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.adapters.db.engine import create_engine

NAMESPACE = uuid.UUID("6f1d0c1e-5b7a-4c39-9a51-0b0a2d6f4e11")
CREATOR_EMAIL = "board-fixture@example.test"
T0 = datetime(2026, 11, 2, tzinfo=UTC)

type Move = tuple[str, float]  # (to_column, hours after T0)

# (key, final column, plan mode or None, moves, triage in scope or None)
CARDS: tuple[tuple[str, str, str | None, tuple[Move, ...], bool | None], ...] = (
    (
        "a",
        "done",
        "single",
        (
            ("backlog", -24),
            ("triaged", 2),
            ("ready", 5),
            ("agent_dev", 6),
            ("agent_review", 10),
            ("human_review", 12),
            ("done", 30),
        ),
        True,
    ),
    (
        "b",
        "done",
        "multi",
        (
            ("backlog", 0),
            ("triaged", 3),
            ("ready", 8),
            ("agent_dev", 9),
            ("agent_review", 40),
            ("changes_requested", 72),
            ("agent_dev", 74),
            ("agent_review", 90),
            ("human_review", 96),
            ("done", 98),
        ),
        True,
    ),
    ("c", "agent_dev", "single", (("backlog", 100), ("triaged", 101), ("agent_dev", 168)), True),
    ("d", "blocked", None, (("backlog", 50), ("blocked", 60)), None),
    ("e", "human_review", "multi", (("backlog", 120), ("human_review", 240)), True),
    ("f", "out_of_scope", None, (("backlog", 130), ("out_of_scope", 131)), False),
)
# (card, round, outcome, hours after T0 it finished, cost)
REVIEWS = (
    ("a", 0, "approved", 12, 0.4),
    ("b", 0, "changes_requested", 72, 0.6),
    ("b", 1, "approved", 96, 0.5),
    ("e", 0, "approved", 240, 0.3),
)
# (card, hours after T0 it finished, cost)
DEV_RUNS = (("a", 10, 1.25), ("b", 40, 2.5), ("b", 90, 1.0))


async def _exec(connection: AsyncConnection, sql: str, *args: object) -> None:
    """asyncpg-style `$n` parameters over SQLAlchemy."""
    params = {f"p{index}": value for index, value in enumerate(args, start=1)}
    await connection.execute(text(re.sub(r"\$(\d+)", r":p\1", sql)), params)


def _id(*parts: object) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, "/".join(str(p) for p in parts))


def _at(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


async def insert_board(owner_url: str) -> uuid.UUID:
    """Insert the board facts (as the database owner); returns the creating user's id."""
    creator = _id("user")
    engine = create_engine(owner_url, pool_size=1)
    try:
        async with engine.begin() as connection:
            await _exec(
                connection,
                "INSERT INTO users (id, email, role) VALUES ($1, $2, 'maintainer')"
                " ON CONFLICT DO NOTHING",
                creator,
                CREATOR_EMAIL,
            )
            for key, column, mode, moves, in_scope in CARDS:
                await _card(connection, creator, key, column, mode, moves, in_scope)
            for key, round_, outcome, hours, cost in REVIEWS:
                await _run(connection, key, "review", round_, outcome, hours, cost)
            for index, (key, hours, cost) in enumerate(DEV_RUNS):
                await _run(connection, key, "dev", 0, f"dev-{index}", hours, cost)
    finally:
        await engine.dispose()
    return creator


async def _card(
    connection: AsyncConnection,
    creator: uuid.UUID,
    key: str,
    column: str,
    mode: str | None,
    moves: tuple[Move, ...],
    in_scope: bool | None,
) -> None:
    task = _id("task", key)
    created = _at(moves[0][1])
    await _exec(
        connection,
        'INSERT INTO dev_tasks (id, title, "column", rank, created_by, created_at, updated_at)'
        " VALUES ($1, $2, $3, $4, $5, $6, $6) ON CONFLICT DO NOTHING",
        task,
        f"Fixture card {key}",
        column,
        f"a{key}",
        creator,
        created,
    )
    previous: str | None = None
    for index, (to_column, hours) in enumerate(moves):
        await _exec(
            connection,
            "INSERT INTO task_events (id, task_id, at, kind, actor_kind, from_column, to_column)"
            " VALUES ($1, $2, $3, $4, 'system', $5, $6) ON CONFLICT DO NOTHING",
            _id("event", key, index),
            task,
            _at(hours),
            "created" if index == 0 else "moved",
            previous,
            to_column,
        )
        previous = to_column
    if mode is not None:
        await _exec(
            connection,
            "INSERT INTO task_plans (id, task_id, mode, budget_usd, budget_minutes, risk,"
            " created_at, updated_at) VALUES ($1, $2, $3, 5, 60, 'low', $4, $4)"
            " ON CONFLICT DO NOTHING",
            _id("plan", key),
            task,
            mode,
            created + timedelta(hours=1),
        )
    if in_scope is not None:
        await _exec(
            connection,
            "INSERT INTO task_triage (id, task_id, in_scope, created_at)"
            " VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
            _id("triage", key),
            task,
            in_scope,
            created + timedelta(minutes=30),
        )


async def _run(
    connection: AsyncConnection,
    key: str,
    kind: str,
    round_: int,
    outcome: str,
    hours: float,
    cost: float,
) -> None:
    finished = _at(hours)
    await _exec(
        connection,
        "INSERT INTO runs (id, task_id, kind, status, round, cost_usd, outcome, started_at,"
        " finished_at, created_at) VALUES ($1, $2, $3, 'succeeded', $4, $5, $6, $7, $8, $7)"
        " ON CONFLICT DO NOTHING",
        _id("run", key, kind, round_, hours),
        _id("task", key),
        kind,
        round_,
        cost,
        outcome if kind == "review" else None,
        finished - timedelta(hours=1),
        finished,
    )
