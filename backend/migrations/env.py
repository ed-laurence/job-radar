"""Alembic environment (async). The URL comes from Settings, the only env reader."""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.adapters.db.engine import async_url
from app.adapters.db.tables import metadata
from app.settings import Settings

# Partitions and Alembic's own table are managed outside the declarative metadata.
_UNMANAGED_PREFIXES = ("web_events_",)


def _include_name(name: str | None, type_: str, _parents: object) -> bool:
    if type_ == "table" and name is not None:
        return not name.startswith(_UNMANAGED_PREFIXES)
    return True


def _url() -> str:
    configured = context.config.get_main_option("sqlalchemy.url")
    if configured:
        return async_url(configured)
    database_url = Settings().database_url
    if database_url is None:
        raise RuntimeError("DATABASE_URL is not set")
    return async_url(database_url.get_secret_value())


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=metadata,
        include_name=_include_name,
        compare_type=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run)
        await connection.commit()
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=_url(), target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_online())
