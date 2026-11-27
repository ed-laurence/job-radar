"""Experience not on your CV (spec tag Settings), ported from job-seeker's `experience.py`.

A record is the requirement it answers plus one entry per role or project where you did it:
`{context (where), detail (what you did there)}`. Each record is one `experience` row and its
entries are `experience_entries` rows, in the order given. Records reach the scoring, evidence
and kit prompts through the candidate profile (one detail per entry, "context: detail"), and
the evidence test cites them by id as "from your records". A record's `uses` counts the roles
whose stored evidence test shows a must-have from it. Labels are positional: "E<n>" for a
record (oldest first) and "E<n>.<k>" for its entries. Updates and deletes are versioned (409
when stale).

The list also offers entry contexts: the roles on the caller's uploaded CVs ("Title,
Employer"; technical CV first, then leadership) and two fixed choices.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.errors import FieldError, NotFound, ValidationFailed
from app.domain.identity import Principal
from app.domain.jobs.cv_roles import cv_roles
from app.domain.jobs.records import ExperienceItem, RoleRecord
from app.domain.ports import Clock, Ids

MAX_RECORDS = 200
MAX_ENTRIES = 10
MAX_REQUIREMENT = 200
MAX_CONTEXT = 200
MAX_DETAIL = 1500
MAX_CONTEXTS = 64
PROJECTS = ("Personal project", "Other")
CV_SLOTS = ("technical", "leadership")  # the technical CV lists every role separately
_EARLIER = "earlier career"
_REQUIRED = "Required."


@dataclass(frozen=True)
class EntryText:
    context: str
    detail: str


@dataclass(frozen=True, kw_only=True)
class ExperienceDraft:
    requirement: str
    entries: tuple[EntryText, ...]
    role_id: uuid.UUID | None = None


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _too_long(pointer: str, limit: int) -> FieldError:
    return FieldError(pointer, f"At most {limit} characters.")


def clean_draft(draft: ExperienceDraft) -> ExperienceDraft:
    """Whitespace collapsed and blank rows dropped. Raises `ValidationFailed` with a field error
    per problem (pointers into the request body)."""
    errors: list[FieldError] = []
    requirement = _collapse(draft.requirement)
    if not requirement:
        errors.append(FieldError("/requirement", _REQUIRED))
    elif len(requirement) > MAX_REQUIREMENT:
        errors.append(_too_long("/requirement", MAX_REQUIREMENT))
    kept: list[EntryText] = []
    for index, entry in enumerate(draft.entries):
        context, detail = _collapse(entry.context), _collapse(entry.detail)
        if not context and not detail:
            continue
        checks = (("context", context, MAX_CONTEXT), ("detail", detail, MAX_DETAIL))
        for name, value, limit in checks:
            pointer = f"/entries/{index}/{name}"
            if not value:
                errors.append(FieldError(pointer, _REQUIRED))
            elif len(value) > limit:
                errors.append(_too_long(pointer, limit))
        kept.append(EntryText(context=context, detail=detail))
    if not kept:
        errors.append(FieldError("/entries", "Add at least one role and what you did there."))
    elif len(kept) > MAX_ENTRIES:
        errors.append(FieldError("/entries", f"At most {MAX_ENTRIES} roles per record."))
    if errors:
        raise ValidationFailed("That experience record isn't complete.", errors=errors)
    return ExperienceDraft(requirement=requirement, entries=tuple(kept), role_id=draft.role_id)


@dataclass(frozen=True, kw_only=True)
class ExperienceEntryRecord:
    """A stored record: its row and its entries, in order."""

    id: uuid.UUID
    requirement: str
    entries: tuple[EntryText, ...]
    version: int
    created_at: datetime
    updated_at: datetime
    role_id: uuid.UUID | None = None


@dataclass(frozen=True, kw_only=True)
class EntryView:
    ref: str
    context: str
    detail: str


@dataclass(frozen=True, kw_only=True)
class ExperienceView:
    """A record as the API shows it: with its labels and how often it was used."""

    id: uuid.UUID
    ref: str
    requirement: str
    entries: tuple[EntryView, ...]
    uses: int
    version: int
    created_at: datetime
    updated_at: datetime
    role_id: uuid.UUID | None = None


@dataclass(frozen=True, kw_only=True)
class ExperienceOverview:
    items: tuple[ExperienceView, ...]
    contexts: tuple[str, ...]


def entry_line(entry: EntryText) -> str:
    """How one entry reads in prompts and quotes: "context: detail"."""
    return f"{entry.context}: {entry.detail}" if entry.context else entry.detail


def profile_item(requirement: str, entries: Sequence[EntryText]) -> ExperienceItem:
    return ExperienceItem(requirement=requirement, details=tuple(entry_line(e) for e in entries))


def as_profile_item(record: ExperienceEntryRecord) -> ExperienceItem:
    """How a record reads in the candidate profile: the requirement and one detail per entry."""
    return profile_item(record.requirement, record.entries)


def view_of(record: ExperienceEntryRecord, position: int, uses: int) -> ExperienceView:
    ref = f"E{position}"
    return ExperienceView(
        id=record.id,
        ref=ref,
        requirement=record.requirement,
        entries=tuple(
            EntryView(ref=f"{ref}.{k}", context=e.context, detail=e.detail)
            for k, e in enumerate(record.entries, start=1)
        ),
        uses=uses,
        version=record.version,
        created_at=record.created_at,
        updated_at=record.updated_at,
        role_id=record.role_id,
    )


def contexts_from(cv_texts: Mapping[str, str]) -> tuple[str, ...]:
    """Roles from the uploaded CVs, newest first as on each CV, technical CV first then
    leadership, without duplicates or "Earlier career" lines; then the fixed projects."""
    found: list[str] = []
    for slot in CV_SLOTS:
        for label in cv_roles(cv_texts.get(slot, "")):
            if label not in found and not label.casefold().startswith(_EARLIER):
                found.append(label)
    roles = found[: MAX_CONTEXTS - len(PROJECTS)]
    return (*roles, *(p for p in PROJECTS if p not in roles))


class ExperienceStore(Protocol):
    async def list_records(self, user_id: uuid.UUID, limit: int) -> list[ExperienceEntryRecord]:
        """Oldest first, each with its entries in order."""
        ...

    async def insert_record(
        self, user_id: uuid.UUID, record_id: uuid.UUID, draft: ExperienceDraft, now: datetime
    ) -> ExperienceEntryRecord: ...

    async def update_record(
        self,
        user_id: uuid.UUID,
        record_id: uuid.UUID,
        version: int,
        draft: ExperienceDraft,
        now: datetime,
    ) -> ExperienceEntryRecord:
        """Replaces the requirement, role and entries. Raises `NotFound` or
        `Conflict(current_version=...)`."""
        ...

    async def delete_record(self, user_id: uuid.UUID, record_id: uuid.UUID, version: int) -> None:
        """Raises `NotFound` or `Conflict(current_version=...)`."""
        ...


class RecordUsage(Protocol):
    async def record_uses(self, user_id: uuid.UUID) -> Mapping[uuid.UUID, int]:
        """Per record id: how many roles' stored evidence tests show a must-have from it."""
        ...


class CvTexts(Protocol):
    async def texts(self, user_id: uuid.UUID) -> dict[str, str]:
        """The caller's stored (guarded, de-identified) document text per slot."""
        ...


class RoleLookup(Protocol):
    async def get_role(self, principal: Principal, role_id: uuid.UUID) -> RoleRecord:
        """Raises `NotFound` for a role the caller can't see."""
        ...


class ExperienceService:
    def __init__(
        self,
        store: ExperienceStore,
        clock: Clock,
        ids: Ids,
        *,
        usage: RecordUsage | None = None,
        cvs: CvTexts | None = None,
        roles: RoleLookup | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids
        self._usage = usage
        self._cvs = cvs
        self._roles = roles

    async def _views(self, user_id: uuid.UUID) -> list[ExperienceView]:
        records = await self._store.list_records(user_id, MAX_RECORDS)
        uses: Mapping[uuid.UUID, int] = {}
        if self._usage is not None:
            uses = await self._usage.record_uses(user_id)
        return [
            view_of(record, position, uses.get(record.id, 0))
            for position, record in enumerate(records, start=1)
        ]

    async def _view(self, user_id: uuid.UUID, record_id: uuid.UUID) -> ExperienceView:
        for view in await self._views(user_id):
            if view.id == record_id:
                return view
        raise missing(record_id)

    async def list_records(self, principal: Principal) -> list[ExperienceView]:
        return await self._views(principal.user_id)

    async def overview(self, principal: Principal) -> ExperienceOverview:
        texts: Mapping[str, str] = {}
        if self._cvs is not None:
            texts = await self._cvs.texts(principal.user_id)
        items = tuple(await self._views(principal.user_id))
        return ExperienceOverview(items=items, contexts=contexts_from(texts))

    async def _checked(self, principal: Principal, draft: ExperienceDraft) -> ExperienceDraft:
        cleaned = clean_draft(draft)
        if cleaned.role_id is not None and self._roles is not None:
            try:
                await self._roles.get_role(principal, cleaned.role_id)
            except NotFound:
                raise ValidationFailed(
                    "That role doesn't exist.",
                    errors=[FieldError("/role_id", "No such role.")],
                ) from None
        return cleaned

    async def create(self, principal: Principal, draft: ExperienceDraft) -> ExperienceView:
        cleaned = await self._checked(principal, draft)
        user_id = principal.user_id
        if len(await self._store.list_records(user_id, MAX_RECORDS)) >= MAX_RECORDS:
            raise ValidationFailed(
                f"You can keep up to {MAX_RECORDS} experience records.",
                errors=[FieldError("/requirement", "Too many records.")],
            )
        record = await self._store.insert_record(
            user_id, self._ids.new_id(), cleaned, self._clock.now()
        )
        return await self._view(user_id, record.id)

    async def update(
        self, principal: Principal, record_id: uuid.UUID, version: int, draft: ExperienceDraft
    ) -> ExperienceView:
        cleaned = await self._checked(principal, draft)
        await self._store.update_record(
            principal.user_id, record_id, version, cleaned, self._clock.now()
        )
        return await self._view(principal.user_id, record_id)

    async def delete(self, principal: Principal, record_id: uuid.UUID, version: int) -> None:
        await self._store.delete_record(principal.user_id, record_id, version)


def missing(record_id: uuid.UUID) -> NotFound:
    return NotFound(f"That experience record ({record_id}) doesn't exist.")
