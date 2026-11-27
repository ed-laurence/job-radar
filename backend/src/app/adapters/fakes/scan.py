"""In-memory scan adapters (no database): dedupe over `MemoryRoles`, runs, a process lock."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager

from app.adapters.fakes.jobs import MemoryRoles
from app.domain.ports.scan import ScanRunRecord


class MemoryKnownRoles:
    def __init__(self, roles: MemoryRoles) -> None:
        self._roles = roles

    async def known(
        self, user_id: uuid.UUID, source: str, external_ids: tuple[str, ...]
    ) -> set[str]:
        wanted = set(external_ids)
        return {
            role.external_id
            for (owner, _), role in self._roles.roles.items()
            if owner == user_id and role.source == source and role.external_id in wanted
        }


class MemoryScanAccounts:
    def __init__(self, users: Sequence[uuid.UUID]) -> None:
        self.users = list(users)

    async def active_users(self) -> list[uuid.UUID]:
        return list(self.users)


class MemoryScanRuns:
    def __init__(self) -> None:
        self.runs: dict[uuid.UUID, ScanRunRecord] = {}

    async def save(self, run: ScanRunRecord) -> None:
        self.runs[run.id] = run

    async def last(self) -> ScanRunRecord | None:
        return max(self.runs.values(), key=lambda run: run.started_at, default=None)


class MemoryScanLock:
    def __init__(self) -> None:
        self._held = False

    @asynccontextmanager
    async def hold(self) -> AsyncGenerator[bool]:
        if self._held:
            yield False
            return
        self._held = True
        try:
            await asyncio.sleep(0)
            yield True
        finally:
            self._held = False

    async def is_held(self) -> bool:
        return self._held
