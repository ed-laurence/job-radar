"""The seed feed: a simulated scraper that releases held-back seed roles as new postings.

A released role is found now and was posted 0-120 minutes ago, the offset fixed per external
id so re-runs and tests are deterministic. The pool lives in `role_pool` (Postgres) or in memory.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from app.domain.ports import Clock
from app.domain.ports.scan import ScrapedRole

SOURCE = "seed"
DEFAULT_BATCH = 5
MAX_POSTED_AGO_MINUTES = 120


def posted_offset(external_id: str) -> timedelta:
    digest = hashlib.sha256(external_id.encode("utf-8")).digest()
    return timedelta(minutes=int.from_bytes(digest[:4], "big") % (MAX_POSTED_AGO_MINUTES + 1))


def _text(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def scraped_role(external_id: str, payload: Mapping[str, object], now: datetime) -> ScrapedRole:
    keywords = payload.get("keywords")
    return ScrapedRole(
        source=SOURCE,
        external_id=external_id,
        title=_text(payload, "title") or "Untitled role",
        found_at=now,
        posted_at=now - posted_offset(external_id),
        company=_text(payload, "company"),
        location=_text(payload, "location"),
        work_mode=_text(payload, "work_mode"),
        pay=_text(payload, "pay"),
        url=_text(payload, "url"),
        description=_text(payload, "description"),
        raw={"keywords": cast("list[object]", keywords) if isinstance(keywords, list) else []},
    )


@dataclass(frozen=True, kw_only=True)
class PoolEntry:
    external_id: str
    posted_at: datetime | None
    payload: Mapping[str, object]


class MemorySeedFeed:
    """In-memory pool per user, released oldest-posted first."""

    name = SOURCE

    def __init__(
        self,
        clock: Clock,
        pool: Mapping[uuid.UUID, list[PoolEntry]] | None = None,
        batch: int = DEFAULT_BATCH,
    ) -> None:
        self._clock = clock
        self._batch = batch
        self.pool: dict[uuid.UUID, list[PoolEntry]] = dict(pool or {})

    async def fetch(self, user_id: uuid.UUID, since: datetime | None) -> list[ScrapedRole]:
        del since  # the pool has no posting window: every held-back role is new
        waiting = sorted(self.pool.get(user_id, []), key=_order)
        chosen, self.pool[user_id] = waiting[: self._batch], waiting[self._batch :]
        now = self._clock.now()
        return [scraped_role(entry.external_id, entry.payload, now) for entry in chosen]

    async def remaining(self) -> int:
        return sum(len(entries) for entries in self.pool.values())


def _order(entry: PoolEntry) -> tuple[bool, datetime | None, str]:
    return (entry.posted_at is None, entry.posted_at, entry.external_id)
