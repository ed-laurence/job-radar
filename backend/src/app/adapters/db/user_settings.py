"""Postgres adapter for `user_settings` (one `jsonb` document per user, under RLS)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.adapters.db.engine import user_transaction
from app.adapters.db.jobs_mapping import Sessions
from app.adapters.db.tables_platform import UserSettings
from app.domain.errors import Conflict
from app.domain.user_settings import JsonMap, StoredSettings

STALE = "Your settings changed since you read them."


class PgUserSettings:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def load(self, user_id: uuid.UUID) -> StoredSettings | None:
        async with user_transaction(self._sessions, user_id) as session:
            row = await session.get(UserSettings, user_id)
            if row is None:
                return None
            return StoredSettings(
                data=dict(row.settings), version=row.version, updated_at=row.updated_at
            )

    async def save(self, user_id: uuid.UUID, data: JsonMap, version: int) -> StoredSettings:
        values = dict(data)
        async with user_transaction(self._sessions, user_id) as session:
            current = await session.scalar(
                select(UserSettings.version).where(UserSettings.user_id == user_id)
            )
            if current is None:
                if version != 1:
                    raise Conflict(STALE, current_version=1)
                stmt = (
                    insert(UserSettings)
                    .values(user_id=user_id, settings=values, version=2)
                    .on_conflict_do_nothing(index_elements=["user_id"])
                    .returning(UserSettings)
                )
            else:
                stmt = (
                    update(UserSettings)
                    .where(UserSettings.user_id == user_id, UserSettings.version == version)
                    .values(
                        settings=values, version=UserSettings.version + 1, updated_at=func.now()
                    )
                    .returning(UserSettings)
                )
            row = await session.scalar(stmt)
            if row is None:
                raise Conflict(STALE, current_version=current)
            await session.refresh(row)
            return StoredSettings(
                data=dict(row.settings), version=row.version, updated_at=row.updated_at
            )
