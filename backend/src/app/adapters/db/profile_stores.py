"""Postgres stores for experience records and the LinkedIn connections import. Every query runs
under row-level security for the caller (`user_transaction`)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import Boolean, delete, func, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.engine import user_transaction
from app.adapters.db.jobs_mapping import Sessions
from app.adapters.db.tables_jobs import Contact, Experience, ExperienceEntry
from app.domain.errors import Conflict
from app.domain.jobs.experience import (
    EntryText,
    ExperienceDraft,
    ExperienceEntryRecord,
    missing,
)
from app.domain.jobs.ports import Values

_IMPORT_CHUNK = 500
# Entries have no position column: each gets `created_at` one microsecond after the one before,
# so ordering by (created_at, id) keeps the order they were given in.
_STEP = timedelta(microseconds=1)


def _pairs(draft: ExperienceDraft) -> list[tuple[str, str]]:
    return [(entry.context, entry.detail) for entry in draft.entries]


def experience_record(
    row: Experience, entries: Sequence[tuple[str | None, str]]
) -> ExperienceEntryRecord:
    """A record from its row and its entries, in order."""
    return ExperienceEntryRecord(
        id=row.id,
        requirement=row.requirement,
        entries=tuple(
            EntryText(context=context or "", detail=detail) for context, detail in entries
        ),
        role_id=row.role_id,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PgExperience:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def list_records(self, user_id: uuid.UUID, limit: int) -> list[ExperienceEntryRecord]:
        async with user_transaction(self._sessions, user_id) as session:
            rows = list(
                await session.scalars(
                    select(Experience).order_by(Experience.created_at, Experience.id).limit(limit)
                )
            )
            by_id: dict[uuid.UUID, list[tuple[str | None, str]]] = {row.id: [] for row in rows}
            if rows:
                entries = await session.execute(
                    select(
                        ExperienceEntry.experience_id,
                        ExperienceEntry.context,
                        ExperienceEntry.detail,
                    )
                    .where(ExperienceEntry.experience_id.in_(list(by_id)))
                    .order_by(ExperienceEntry.created_at, ExperienceEntry.id)
                )
                for experience_id, context, detail in entries:
                    by_id[experience_id].append((context, detail))
            return [experience_record(row, by_id[row.id]) for row in rows]

    async def insert_record(
        self, user_id: uuid.UUID, record_id: uuid.UUID, draft: ExperienceDraft, now: datetime
    ) -> ExperienceEntryRecord:
        async with user_transaction(self._sessions, user_id) as session:
            row = Experience(
                id=record_id,
                user_id=user_id,
                requirement=draft.requirement,
                role_id=draft.role_id,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            await session.flush()
            pairs = _pairs(draft)
            self._add_entries(session, user_id, record_id, pairs, now)
            await session.flush()
            await session.refresh(row)
            return experience_record(row, pairs)

    async def update_record(
        self,
        user_id: uuid.UUID,
        record_id: uuid.UUID,
        version: int,
        draft: ExperienceDraft,
        now: datetime,
    ) -> ExperienceEntryRecord:
        async with user_transaction(self._sessions, user_id) as session:
            row = await session.scalar(
                update(Experience)
                .where(Experience.id == record_id, Experience.version == version)
                .values(
                    requirement=draft.requirement,
                    role_id=draft.role_id,
                    version=Experience.version + 1,
                    updated_at=now,
                )
                .returning(Experience)
            )
            if row is None:
                await self._diagnose(session, record_id)
                raise missing(record_id)
            await session.execute(
                delete(ExperienceEntry).where(ExperienceEntry.experience_id == record_id)
            )
            pairs = _pairs(draft)
            self._add_entries(session, user_id, record_id, pairs, now)
            return experience_record(row, pairs)

    async def delete_record(self, user_id: uuid.UUID, record_id: uuid.UUID, version: int) -> None:
        async with user_transaction(self._sessions, user_id) as session:
            current = await session.scalar(
                select(Experience.version).where(Experience.id == record_id)
            )
            if current is None:
                raise missing(record_id)
            if current != version:
                raise Conflict("The record changed since you read it.", current_version=current)
            await session.execute(
                delete(ExperienceEntry).where(ExperienceEntry.experience_id == record_id)
            )
            await session.execute(delete(Experience).where(Experience.id == record_id))

    @staticmethod
    def _add_entries(
        session: AsyncSession,
        user_id: uuid.UUID,
        record_id: uuid.UUID,
        pairs: Sequence[tuple[str, str]],
        now: datetime,
    ) -> None:
        for position, (context, detail) in enumerate(pairs):
            at = now + _STEP * position
            session.add(
                ExperienceEntry(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    experience_id=record_id,
                    context=context,
                    detail=detail,
                    created_at=at,
                    updated_at=at,
                )
            )

    @staticmethod
    async def _diagnose(session: AsyncSession, record_id: uuid.UUID) -> None:
        current = await session.scalar(select(Experience.version).where(Experience.id == record_id))
        if current is not None:
            raise Conflict("The record changed since you read it.", current_version=current)


_IMPORTED = (
    "first_name",
    "last_name",
    "company",
    "company_norm",
    "position",
    "connected_on",
)


class PgContactImport:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def upsert_connections(
        self, user_id: uuid.UUID, rows: Sequence[Values], now: datetime
    ) -> tuple[int, int]:
        inserted = updated = 0
        async with user_transaction(self._sessions, user_id) as session:
            for start in range(0, len(rows), _IMPORT_CHUNK):
                chunk = [
                    {**row, "user_id": user_id, "created_at": now, "updated_at": now}
                    for row in rows[start : start + _IMPORT_CHUNK]
                ]
                stmt = insert(Contact).values(chunk)
                excluded = stmt.excluded
                set_values: dict[str, object] = {name: excluded[name] for name in _IMPORTED}
                set_values["email"] = func.coalesce(excluded.email, Contact.email)
                set_values["deleted_at"] = None
                set_values["updated_at"] = now
                set_values["version"] = Contact.version + 1
                stmt = stmt.on_conflict_do_update(
                    index_elements=[Contact.user_id, Contact.url], set_=set_values
                ).returning(literal_column("(xmax = 0)", type_=Boolean))
                for (was_inserted,) in await session.execute(stmt):
                    if was_inserted:
                        inserted += 1
                    else:
                        updated += 1
        return inserted, updated

    async def delete_all(self, user_id: uuid.UUID) -> int:
        async with user_transaction(self._sessions, user_id) as session:
            result = await session.execute(
                delete(Contact).where(Contact.user_id == user_id).returning(Contact.id)
            )
            return len(result.all())

    async def count(self, user_id: uuid.UUID) -> int:
        async with user_transaction(self._sessions, user_id) as session:
            found = await session.scalar(
                select(func.count())
                .select_from(Contact)
                .where(Contact.user_id == user_id, Contact.deleted_at.is_(None))
            )
            return int(found or 0)
