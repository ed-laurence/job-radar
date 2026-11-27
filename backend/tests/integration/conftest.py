"""Real Postgres. `DATABASE_URL` must point at a server where we may create databases and roles
(the docker-compose `postgres` service, or CI's service container). Each session migrates a
fresh database and runs the app as a non-owner login role, so FORCE RLS actually applies."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.adapters.db.engine import create_engine, plain_url, session_factory
from app.settings import REPO_ROOT

APP_LOGIN = "jobradar_app_test"
APP_PASSWORD = "local-test-only"


async def _connect(url: str) -> asyncpg.Connection:
    return await asyncpg.connect(url)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]


@dataclass(frozen=True)
class Database:
    owner_url: str
    app_url: str


def _admin_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("integration tests need DATABASE_URL")
    return plain_url(url)


async def _create(admin_url: str, name: str) -> None:
    connection = await _connect(admin_url)
    try:
        await connection.execute(f'CREATE DATABASE "{name}"')
        await connection.execute(
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
            f"'{APP_LOGIN}') THEN CREATE ROLE {APP_LOGIN} LOGIN PASSWORD '{APP_PASSWORD}'; "
            "END IF; END $$"
        )
    finally:
        await connection.close()


async def _drop(admin_url: str, name: str) -> None:
    connection = await _connect(admin_url)
    try:
        await connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await connection.close()


def _alembic(url: str) -> Config:
    config = Config(str(REPO_ROOT / "backend" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture(scope="session")
def database() -> Generator[Database]:
    admin = _admin_url()
    name = f"jobradar_it_{uuid.uuid4().hex[:8]}"
    asyncio.run(_create(admin, name))
    owner_url = make_url(admin).set(database=name).render_as_string(hide_password=False)
    command.upgrade(_alembic(owner_url), "head")

    async def grant() -> None:
        connection = await _connect(owner_url)
        try:
            await connection.execute(f"GRANT jobradar_app TO {APP_LOGIN}")
        finally:
            await connection.close()

    asyncio.run(grant())
    app_url = (
        make_url(owner_url)
        .set(username=APP_LOGIN, password=APP_PASSWORD)
        .render_as_string(hide_password=False)
    )
    try:
        yield Database(owner_url=owner_url, app_url=app_url)
    finally:
        asyncio.run(_drop(admin, name))


@pytest.fixture
async def owner(database: Database) -> AsyncGenerator[asyncpg.Connection]:
    connection = await _connect(database.owner_url)
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
async def app_engine(database: Database) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(database.app_url, pool_size=2)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def sessions(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return session_factory(app_engine)


@pytest.fixture
async def two_users(owner: asyncpg.Connection) -> tuple[uuid.UUID, uuid.UUID]:
    first, second = uuid.uuid4(), uuid.uuid4()
    await owner.execute(
        "INSERT INTO users (id, email, role) VALUES ($1, $3, 'maintainer'), ($2, $4, 'user')",
        first,
        second,
        f"{first.hex[:8]}@example.test",
        f"{second.hex[:8]}@example.test",
    )
    return first, second
