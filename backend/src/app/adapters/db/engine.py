"""Engine and session factories, plus the per-transaction `app.user_id` (ARCH-05, SEC-03)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_SET_USER = text("SELECT set_config('app.user_id', :user_id, true)")
_BYPASSES_RLS = text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")


class PrivilegedDatabaseRole(RuntimeError):
    """The app's database role would silently bypass row-level security (SEC-03)."""


async def assert_rls_enforced(engine: AsyncEngine) -> None:
    """Fail closed if the serving role is a superuser or has BYPASSRLS.

    Postgres skips RLS for such roles even with FORCE ROW LEVEL SECURITY, so tenant isolation
    would vanish without any error. Migrations and the seed loader run as the owner and don't
    call this; the API process must connect as a login granted `jobradar_app`.
    """
    async with engine.connect() as connection:
        bypasses = (await connection.execute(_BYPASSES_RLS)).scalar_one_or_none()
    if bypasses:
        raise PrivilegedDatabaseRole(
            "The database role bypasses row-level security. Connect the API as a login "
            "granted jobradar_app (see scripts/dev.sh), not as a superuser or owner."
        )


def async_url(url: str) -> str:
    """Accept `postgres://` / `postgresql://` URLs and select the asyncpg driver."""
    parsed = make_url(url)
    if parsed.drivername in {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        parsed = parsed.set(drivername="postgresql+asyncpg")
    return parsed.render_as_string(hide_password=False)


def plain_url(url: str) -> str:
    """The libpq-style URL asyncpg's own `connect()` expects (for the LISTEN connection)."""
    parsed = make_url(url).set(drivername="postgresql")
    return parsed.render_as_string(hide_password=False)


def create_engine(url: str, *, pool_size: int = 5) -> AsyncEngine:
    return create_async_engine(
        async_url(url),
        pool_size=pool_size,
        max_overflow=0,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def set_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Scope RLS to `user_id` for the current transaction only (`is_local = true`)."""
    await session.execute(_SET_USER, {"user_id": str(user_id)})


@asynccontextmanager
async def user_transaction(
    factory: async_sessionmaker[AsyncSession], user_id: uuid.UUID
) -> AsyncGenerator[AsyncSession]:
    """One transaction under RLS for `user_id`. Commits on success, rolls back on error."""
    async with factory() as session, session.begin():
        await set_user(session, user_id)
        yield session


@asynccontextmanager
async def system_transaction(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession]:
    """For Sys tables only (rate limits, budgets, web events). No `app.user_id`, so every
    user-owned and shared table stays invisible under RLS."""
    async with factory() as session, session.begin():
        yield session
