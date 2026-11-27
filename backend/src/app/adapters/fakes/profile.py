"""In-memory fakes for experience records, the LinkedIn import and PDF text (ARCH-04)."""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Sequence
from datetime import datetime

from app.adapters.fakes.jobs import MemoryRows, build, patch
from app.domain.errors import Conflict, FieldError, ValidationFailed
from app.domain.jobs.documents import PdfContent
from app.domain.jobs.experience import ExperienceDraft, ExperienceEntryRecord, missing
from app.domain.jobs.ports import Values
from app.domain.jobs.records import ContactRecord

type Key = tuple[uuid.UUID, uuid.UUID]


class MemoryExperience:
    def __init__(self) -> None:
        self.records: dict[Key, ExperienceEntryRecord] = {}

    async def list_records(self, user_id: uuid.UUID, limit: int) -> list[ExperienceEntryRecord]:
        owned = [r for (owner, _), r in self.records.items() if owner == user_id]
        owned.sort(key=lambda r: (r.created_at, str(r.id)))
        return owned[:limit]

    def owned(self, user_id: uuid.UUID) -> list[ExperienceEntryRecord]:
        return [r for (owner, _), r in sorted(self.records.items(), key=_order) if owner == user_id]

    async def insert_record(
        self, user_id: uuid.UUID, record_id: uuid.UUID, draft: ExperienceDraft, now: datetime
    ) -> ExperienceEntryRecord:
        record = ExperienceEntryRecord(
            id=record_id,
            requirement=draft.requirement,
            entries=draft.entries,
            role_id=draft.role_id,
            version=1,
            created_at=now,
            updated_at=now,
        )
        self.records[(user_id, record_id)] = record
        return record

    async def update_record(
        self,
        user_id: uuid.UUID,
        record_id: uuid.UUID,
        version: int,
        draft: ExperienceDraft,
        now: datetime,
    ) -> ExperienceEntryRecord:
        current = self._current(user_id, record_id, version)
        record = dataclasses.replace(
            current,
            requirement=draft.requirement,
            entries=draft.entries,
            role_id=draft.role_id,
            version=current.version + 1,
            updated_at=now,
        )
        self.records[(user_id, record_id)] = record
        return record

    async def delete_record(self, user_id: uuid.UUID, record_id: uuid.UUID, version: int) -> None:
        self._current(user_id, record_id, version)
        del self.records[(user_id, record_id)]

    def _current(
        self, user_id: uuid.UUID, record_id: uuid.UUID, version: int
    ) -> ExperienceEntryRecord:
        current = self.records.get((user_id, record_id))
        if current is None:
            raise missing(record_id)
        if current.version != version:
            raise Conflict("The record changed since you read it.", current_version=current.version)
        return current


def _order(item: tuple[Key, ExperienceEntryRecord]) -> tuple[datetime, str]:
    return (item[1].created_at, str(item[1].id))


class MemoryContactImport:
    """The import over the in-memory contact rows (the same ones the Contacts routes use)."""

    def __init__(self, rows: MemoryRows[ContactRecord]) -> None:
        self._rows = rows

    async def upsert_connections(
        self, user_id: uuid.UUID, rows: Sequence[Values], now: datetime
    ) -> tuple[int, int]:
        by_url = {
            record.url: key
            for key, record in self._rows.rows.items()
            if key[0] == user_id and record.url
        }
        inserted = updated = 0
        for values in rows:
            key = by_url.get(str(values["url"]))
            if key is None:
                record = build(
                    ContactRecord,
                    {"status": "new", "version": 1, "created_at": now, "updated_at": now, **values},
                )
                self._rows.rows[(user_id, record.id)] = record
                inserted += 1
                continue
            current = self._rows.rows[key]
            changes = {k: v for k, v in values.items() if k != "id"}
            if changes.get("email") is None:
                changes["email"] = current.email
            self._rows.rows[key] = patch(
                current,
                {**changes, "deleted_at": None, "updated_at": now, "version": current.version + 1},
            )
            updated += 1
        return inserted, updated

    async def delete_all(self, user_id: uuid.UUID) -> int:
        keys = [key for key in self._rows.rows if key[0] == user_id]
        for key in keys:
            del self._rows.rows[key]
        return len(keys)

    async def count(self, user_id: uuid.UUID) -> int:
        return sum(
            1
            for (owner, _), record in self._rows.rows.items()
            if owner == user_id and record.deleted_at is None
        )


class FakePdfText:
    """Reads the text after `%PDF-fake` markers: `%PDF-fake pages=N` then the text. Anything
    with `/Encrypt` is refused, as pypdf would."""

    async def extract(self, data: bytes, max_pages: int) -> PdfContent:
        body = data.decode("latin-1")
        if "/Encrypt" in body:
            raise ValidationFailed(
                "That PDF couldn't be read.", errors=[FieldError("/data", "Encrypted.")]
            )
        first, _, text = body.partition("\n")
        pages = 1
        if "pages=" in first:
            pages = int(first.split("pages=", 1)[1].split()[0])
        return PdfContent(pages=pages, text=text if pages <= max_pages else "")
