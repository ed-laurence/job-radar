"""`profile_documents`: the per-user document manifest, under row-level security."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.engine import user_transaction
from app.adapters.db.tables_scan import ProfileDocuments


class PgManifests:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def load(self, user_id: uuid.UUID) -> dict[str, object] | None:
        async with user_transaction(self._sessions, user_id) as session:
            # Filtered by user as well as RLS: the seed runs with the owner URL, which skips RLS.
            found = await session.scalar(
                select(ProfileDocuments.manifest).where(ProfileDocuments.user_id == user_id)
            )
        return dict(found) if found is not None else None

    async def save(self, user_id: uuid.UUID, manifest: Mapping[str, object]) -> None:
        values = {"user_id": user_id, "manifest": dict(manifest)}
        stmt = insert(ProfileDocuments).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[ProfileDocuments.user_id],
            set_={"manifest": stmt.excluded.manifest, "updated_at": func.now()},
        )
        async with user_transaction(self._sessions, user_id) as session:
            await session.execute(stmt)
