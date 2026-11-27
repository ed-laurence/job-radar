"""Contacts and campaign targets: CRUD with soft delete and optimistic `version` (ARCH-11)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping

from app.domain.errors import FieldError, NotFound, ValidationFailed
from app.domain.identity import Principal
from app.domain.jobs.pagination import decode_cursor, to_page
from app.domain.jobs.ports import ContactStore, TargetStore, TextSearch, VersionedStore
from app.domain.jobs.records import ContactRecord, Page, TargetRecord
from app.domain.ports import Clock, Ids

_SUFFIXES = re.compile(
    r"\b(pty|ltd|limited|inc|llc|plc|group|holdings|australia|co|corp|corporation)\b\.?",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^a-z0-9]+")
# Contacts still to approach (the Outreach tab and its count).
OUTREACH_STATUSES = frozenset({"new", "drafted"})

CONTACT_FIELDS = frozenset(
    {
        "first_name",
        "last_name",
        "email",
        "url",
        "company",
        "position",
        "connected_on",
        "closeness",
        "status",
        "message",
        "notes",
    }
)
TARGET_FIELDS = frozenset(
    {"name", "lane", "why", "approach", "caveat", "sources", "status", "notes"}
)


CONTACT_SEARCH = ("first_name", "last_name", "company", "position", "email")
TARGET_SEARCH = ("name", "why", "approach", "caveat", "notes")
MAX_SEARCH = 100


def search_for(
    q: str | None, columns: tuple[str, ...], *, full_name: bool = False
) -> TextSearch | None:
    """`q` with whitespace runs collapsed; None (no filter) when it's empty or blank."""
    term = " ".join((q or "").split())[:MAX_SEARCH]
    return TextSearch(term=term, columns=columns, full_name=full_name) if term else None


def company_key(name: str | None) -> str | None:
    """Normalised organisation name for matching ("Acme Pty Ltd" == "ACME")."""
    if not name:
        return None
    stripped = _SUFFIXES.sub(" ", name.lower())
    key = _NON_WORD.sub(" ", stripped).strip()
    return key or _NON_WORD.sub(" ", name.lower()).strip() or None


class _Versioned[R]:
    def __init__(self, store: VersionedStore[R], clock: Clock, ids: Ids, noun: str) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids
        self._noun = noun

    async def page(
        self,
        principal: Principal,
        filters: Mapping[str, object],
        cursor: str | None,
        limit: int,
        search: TextSearch | None = None,
    ) -> Page[R]:
        offset = decode_cursor(cursor)
        live = {key: value for key, value in filters.items() if value is not None}
        rows = await self._store.list_rows(principal.user_id, live, offset, limit + 1, search)
        return to_page(rows, offset, limit)

    async def get(self, principal: Principal, row_id: uuid.UUID) -> R:
        row = await self._store.get_row(principal.user_id, row_id)
        if row is None:
            raise NotFound(f"That {self._noun} doesn't exist.")
        return row

    async def insert(self, principal: Principal, values: Mapping[str, object]) -> R:
        now = self._clock.now()
        row = {"id": self._ids.new_id(), "created_at": now, "updated_at": now, **values}
        return await self._store.insert_row(principal.user_id, row)

    async def update(
        self, principal: Principal, row_id: uuid.UUID, version: int, values: Mapping[str, object]
    ) -> R:
        return await self._store.update_row(
            principal.user_id, row_id, version, {**values, "updated_at": self._clock.now()}
        )

    async def delete(self, principal: Principal, row_id: uuid.UUID, version: int) -> None:
        now = self._clock.now()
        await self._store.update_row(
            principal.user_id, row_id, version, {"deleted_at": now, "updated_at": now}
        )


class ContactService:
    def __init__(self, store: ContactStore, clock: Clock, ids: Ids) -> None:
        self._rows = _Versioned[ContactRecord](store, clock, ids, "contact")
        self._clock = clock

    async def list_contacts(
        self,
        principal: Principal,
        status: str | None,
        company: str | None,
        cursor: str | None,
        limit: int,
        *,
        needs_outreach: bool = False,
        q: str | None = None,
    ) -> Page[ContactRecord]:
        """`needs_outreach` keeps contacts still to approach (new or drafted). `q` matches
        first, last or full name, company, position or email (case-insensitive substring)."""
        filters: dict[str, object] = {"status": status, "company_norm": company_key(company)}
        if needs_outreach:
            wanted = OUTREACH_STATUSES if status is None else (OUTREACH_STATUSES & {status})
            filters["status"] = tuple(sorted(wanted)) or ("none",)
        search = search_for(q, CONTACT_SEARCH, full_name=True)
        return await self._rows.page(principal, filters, cursor, limit, search)

    async def get_contact(self, principal: Principal, contact_id: uuid.UUID) -> ContactRecord:
        return await self._rows.get(principal, contact_id)

    async def create_contact(
        self, principal: Principal, fields: Mapping[str, object]
    ) -> ContactRecord:
        values = _allowed(fields, CONTACT_FIELDS)
        values.setdefault("status", "new")
        values["company_norm"] = company_key(_text(values.get("company")))
        return await self._rows.insert(principal, values)

    async def update_contact(
        self,
        principal: Principal,
        contact_id: uuid.UUID,
        version: int,
        changes: Mapping[str, object],
    ) -> ContactRecord:
        values = _allowed(changes, CONTACT_FIELDS, require_change=True)
        if "company" in values:
            values["company_norm"] = company_key(_text(values["company"]))
        if values.get("status") == "sent":
            current = await self._rows.get(principal, contact_id)
            if current.sent_at is None:
                values["sent_at"] = self._clock.now()
        return await self._rows.update(principal, contact_id, version, values)

    async def delete_contact(
        self, principal: Principal, contact_id: uuid.UUID, version: int
    ) -> None:
        await self._rows.delete(principal, contact_id, version)


class TargetService:
    def __init__(self, store: TargetStore, clock: Clock, ids: Ids) -> None:
        self._rows = _Versioned[TargetRecord](store, clock, ids, "target")
        self._clock = clock

    async def list_targets(
        self,
        principal: Principal,
        status: str | None,
        lane: str | None,
        cursor: str | None,
        limit: int,
        *,
        q: str | None = None,
    ) -> Page[TargetRecord]:
        """`q` matches the name, why, approach, caveat or notes (case-insensitive substring)."""
        filters = {"status": status, "lane": lane}
        search = search_for(q, TARGET_SEARCH)
        return await self._rows.page(principal, filters, cursor, limit, search)

    async def get_target(self, principal: Principal, target_id: uuid.UUID) -> TargetRecord:
        return await self._rows.get(principal, target_id)

    async def create_target(
        self, principal: Principal, fields: Mapping[str, object]
    ) -> TargetRecord:
        values = _allowed(fields, TARGET_FIELDS)
        values.setdefault("status", "suggested")
        values.setdefault("sources", [])
        values["origin"] = "manual"
        values["name_key"] = company_key(_text(values.get("name"))) or ""
        return await self._rows.insert(principal, values)

    async def update_target(
        self,
        principal: Principal,
        target_id: uuid.UUID,
        version: int,
        changes: Mapping[str, object],
    ) -> TargetRecord:
        values = _allowed(changes, TARGET_FIELDS, require_change=True)
        if "name" in values:
            values["name_key"] = company_key(_text(values["name"])) or ""
        if values.get("status") == "approached":
            current = await self._rows.get(principal, target_id)
            if current.approached_at is None:
                values["approached_at"] = self._clock.now()
        return await self._rows.update(principal, target_id, version, values)

    async def delete_target(self, principal: Principal, target_id: uuid.UUID, version: int) -> None:
        await self._rows.delete(principal, target_id, version)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _allowed(
    fields: Mapping[str, object], allowed: frozenset[str], *, require_change: bool = False
) -> dict[str, object]:
    errors = [FieldError(f"/{name}", "Unknown field.") for name in fields if name not in allowed]
    if require_change and not fields:
        raise ValidationFailed("Change at least one field.")
    for required in ("name", "first_name", "lane", "status"):
        if required in fields and fields[required] is None:
            errors.append(FieldError(f"/{required}", "This field can't be empty."))
    if errors:
        raise ValidationFailed("Check the highlighted fields.", errors=errors)
    return dict(fields)
