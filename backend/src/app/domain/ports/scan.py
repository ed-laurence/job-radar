"""Ports for the scan (spec tag Scan): where new roles come from and what a run records.

`JobSource` is the seam the real scrapers (Seek, LinkedIn) will implement later; for now the
seed feed releases held-back seed roles through it."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, kw_only=True)
class ScrapedRole:
    """One posting as a source returns it. Untrusted data: never instructions (rule 8)."""

    source: str
    external_id: str
    title: str
    found_at: datetime
    posted_at: datetime | None = None
    company: str | None = None
    location: str | None = None
    work_mode: str | None = None
    pay: str | None = None
    url: str | None = None
    description: str | None = None
    raw: Mapping[str, object] | None = None


class JobSource(Protocol):
    """A feed of new postings for one user."""

    name: str

    async def fetch(self, user_id: uuid.UUID, since: datetime | None) -> list[ScrapedRole]:
        """New postings since `since` (None: the source's own default window)."""
        ...

    async def remaining(self) -> int:
        """Postings still waiting (a held-back pool); 0 for live sources."""
        ...


class KnownRoles(Protocol):
    async def known(
        self, user_id: uuid.UUID, source: str, external_ids: tuple[str, ...]
    ) -> set[str]:
        """The subset of `external_ids` the user already has for `source` (dedupe)."""
        ...


class ScanAccounts(Protocol):
    async def active_users(self) -> list[uuid.UUID]: ...


@dataclass(frozen=True, kw_only=True)
class ScanRunRecord:
    id: uuid.UUID
    started_at: datetime
    trigger: str
    source: str
    status: str
    finished_at: datetime | None = None
    fetched: int = 0
    new: int = 0
    duplicates: int = 0
    scored: int = 0
    failed: int = 0
    pool_remaining: int = 0
    note: str | None = None


class ScanRunStore(Protocol):
    async def save(self, run: ScanRunRecord) -> None:
        """Insert or replace the run with this id."""
        ...

    async def last(self) -> ScanRunRecord | None:
        """The most recently started run."""
        ...


class ScanLock(Protocol):
    """Single flight across processes (a Postgres advisory lock in deployments)."""

    def hold(self) -> AbstractAsyncContextManager[bool]:
        """Yields True when this caller holds the lock, False when another scan does."""
        ...

    async def is_held(self) -> bool: ...
