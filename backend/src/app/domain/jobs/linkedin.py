"""LinkedIn connections import (spec tag Contacts), ported from job-seeker's `contacts.py`.

The upload is LinkedIn's `Connections.csv`, which starts with a few "Notes" lines before the
header row. It is untrusted: at most 2 MB and 10,000 rows, parsed with the `csv` module (quotes,
embedded commas and newlines), and every cell that starts with `=`, `+`, `-`, `@` (or a tab or
carriage return) is neutralised with a leading apostrophe before it is stored, so a later
spreadsheet export can't run it as a formula. Rows are upserted by profile URL.
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol

from app.domain.errors import FieldError, ValidationFailed
from app.domain.identity import Principal
from app.domain.jobs.crm import company_key
from app.domain.jobs.documents import ImportSummary, decode_upload, sanitise_filename
from app.domain.jobs.ports import Values
from app.domain.ports import Clock, Ids

MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_ROWS = 10_000
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
_COLUMNS = {
    "first name": "first_name",
    "last name": "last_name",
    "url": "url",
    "email address": "email",
    "company": "company",
    "position": "position",
    "connected on": "connected_on",
}
_LIMITS = {"first_name": 100, "last_name": 100, "email": 254, "company": 200, "position": 200}
_DATE_FORMATS = ("%d %b %Y", "%d-%b-%y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%y")
_EMAIL = re.compile(r"^[^@\s'\"<>]+@[^@\s'\"<>]+\.[A-Za-z]{2,}$")
_URL = re.compile(r"^https?://[^\s\"'<>]+$", re.IGNORECASE)


@dataclass(frozen=True, kw_only=True)
class Connection:
    url: str
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    company: str | None = None
    position: str | None = None
    connected_on: date | None = None


@dataclass(frozen=True)
class ParsedConnections:
    rows: tuple[Connection, ...]
    skipped: int


@dataclass(frozen=True, kw_only=True)
class ImportResult:
    imported: int
    updated: int
    skipped: int


def neutralise(cell: str) -> str:
    """A cell safe to store and export: formula-looking values get a leading apostrophe."""
    value = cell.strip()
    return f"'{value}" if value.startswith(FORMULA_STARTS) else value


def parse_date(value: str) -> date | None:
    text = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    return None


def _header_index(rows: Sequence[Sequence[str]]) -> int:
    for index, row in enumerate(rows[:50]):
        names = {cell.strip().lower() for cell in row}
        if {"first name", "last name"} <= names:
            return index
    raise ValidationFailed(
        "That doesn't look like LinkedIn's Connections.csv.",
        errors=[FieldError("/data", "No First Name / Last Name header row.")],
    )


def _text(raw: dict[str, str], name: str) -> str | None:
    value = neutralise(raw.get(name, ""))
    if not value:
        return None
    return value[: _LIMITS[name]] if name in _LIMITS else value


def _connection(raw: dict[str, str]) -> Connection | None:
    url = raw.get("url", "").strip()
    first, last = _text(raw, "first_name"), _text(raw, "last_name")
    if not url or len(url) > 2048 or not _URL.match(url) or not (first or last):
        return None
    email = raw.get("email", "").strip()
    return Connection(
        url=url,
        first_name=first,
        last_name=last,
        email=email if email and len(email) <= 254 and _EMAIL.match(email) else None,
        company=_text(raw, "company"),
        position=_text(raw, "position"),
        connected_on=parse_date(raw.get("connected_on", "")),
    )


def parse_connections_csv(text: str) -> ParsedConnections:
    """Rows after the header (preamble skipped). Rows without a name or an http(s) profile URL
    are skipped; a later duplicate URL replaces an earlier one."""
    try:
        rows = list(csv.reader(io.StringIO(text.replace("\x00", ""), newline="")))
    except csv.Error:
        raise ValidationFailed(
            "That CSV couldn't be read.", errors=[FieldError("/data", "Malformed CSV.")]
        ) from None
    start = _header_index(rows)
    header = [_COLUMNS.get(cell.strip().lower(), "") for cell in rows[start]]
    body = [row for row in rows[start + 1 :] if any(cell.strip() for cell in row)]
    if len(body) > MAX_ROWS:
        raise ValidationFailed(
            "That file has too many rows.", errors=[FieldError("/data", "At most 10,000 rows.")]
        )
    found: dict[str, Connection] = {}
    skipped = 0
    for row in body:
        raw = {name: cell for name, cell in zip(header, row, strict=False) if name}
        connection = _connection(raw)
        if connection is None:
            skipped += 1
            continue
        found[connection.url] = connection
    return ParsedConnections(rows=tuple(found.values()), skipped=skipped)


def decode_csv(data: str) -> str:
    raw = decode_upload(data, MAX_CSV_BYTES)
    return raw.decode("utf-8-sig", errors="replace")


class ContactImportStore(Protocol):
    async def upsert_connections(
        self, user_id: uuid.UUID, rows: Sequence[Values], now: datetime
    ) -> tuple[int, int]:
        """Insert or update by profile URL (reviving a soft-deleted contact); returns
        `(inserted, updated)`. Existing status, message and notes are kept."""
        ...

    async def delete_all(self, user_id: uuid.UUID) -> int:
        """Remove every contact the user owns (row-level security); returns the count."""
        ...

    async def count(self, user_id: uuid.UUID) -> int: ...


class ImportSummaries(Protocol):
    async def record_import(self, user_id: uuid.UUID, summary: ImportSummary) -> None: ...

    async def last_import(self, user_id: uuid.UUID) -> ImportSummary | None: ...


@dataclass(frozen=True)
class LinkedinOverview:
    contacts: int
    last: ImportSummary | None


class LinkedinImportService:
    def __init__(
        self, store: ContactImportStore, summaries: ImportSummaries, clock: Clock, ids: Ids
    ) -> None:
        self._store = store
        self._summaries = summaries
        self._clock = clock
        self._ids = ids

    async def import_csv(self, principal: Principal, filename: str, data: str) -> ImportResult:
        parsed = parse_connections_csv(decode_csv(data))
        now = self._clock.now()
        values: list[Values] = [
            {
                "id": self._ids.new_id(),
                "url": row.url,
                "first_name": row.first_name,
                "last_name": row.last_name,
                "email": row.email,
                "company": row.company,
                "company_norm": company_key(row.company),
                "position": row.position,
                "connected_on": row.connected_on,
            }
            for row in parsed.rows
        ]
        inserted, updated = await self._store.upsert_connections(principal.user_id, values, now)
        result = ImportResult(imported=inserted, updated=updated, skipped=parsed.skipped)
        summary = ImportSummary(
            filename=sanitise_filename(filename, "Connections.csv", ".csv"),
            imported_at=now,
            imported=result.imported,
            updated=result.updated,
            skipped=result.skipped,
        )
        await self._summaries.record_import(principal.user_id, summary)
        return result

    async def delete_all(self, principal: Principal, *, confirm: bool) -> int:
        if not confirm:
            raise ValidationFailed(
                "Confirm to remove all contacts.",
                errors=[FieldError("/confirm", "Must be true.")],
            )
        return await self._store.delete_all(principal.user_id)

    async def overview(self, principal: Principal) -> LinkedinOverview:
        count = await self._store.count(principal.user_id)
        return LinkedinOverview(count, await self._summaries.last_import(principal.user_id))
