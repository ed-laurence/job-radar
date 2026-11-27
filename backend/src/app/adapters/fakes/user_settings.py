"""In-memory `UserSettingsStore` (no database)."""

from __future__ import annotations

import uuid

from app.domain.errors import Conflict
from app.domain.ports import Clock
from app.domain.user_settings import JsonMap, StoredSettings

STALE = "Your settings changed since you read them."


class MemoryUserSettings:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self.rows: dict[uuid.UUID, StoredSettings] = {}

    async def load(self, user_id: uuid.UUID) -> StoredSettings | None:
        return self.rows.get(user_id)

    async def save(self, user_id: uuid.UUID, data: JsonMap, version: int) -> StoredSettings:
        current = self.rows.get(user_id)
        current_version = current.version if current is not None else 1
        if version != current_version:
            raise Conflict(STALE, current_version=current_version)
        saved = StoredSettings(
            data=dict(data), version=current_version + 1, updated_at=self._clock.now()
        )
        self.rows[user_id] = saved
        return saved
