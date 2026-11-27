"""Roles and applications: CRUD, status moves, pipeline order and history (ARCH-05, ARCH-11)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from app.domain.errors import FieldError, GuardBlocked, NotFound, ValidationFailed
from app.domain.identity import Principal
from app.domain.jobs.pagination import decode_cursor, to_page
from app.domain.jobs.ports import NewRoleEvent, RoleQuery, RoleStore, RoleWrite
from app.domain.jobs.ranking import MAX_RANK_CHARS, key_between, spread
from app.domain.jobs.records import Page, RoleEventRecord, RoleRecord
from app.domain.ports import Clock, ContentGuard, Ids
from app.domain.user_settings import PrefsSource, prefs_for

EDITABLE_FIELDS = frozenset(
    {
        "url",
        "title",
        "company",
        "location",
        "work_mode",
        "pay",
        "posted_at",
        "track",
        "mission",
        "applied_at",
        "followed_up_at",
        "notes",
    }
)
REQUIRED_FIELDS = frozenset({"title"})
# The history the API shows; `notified` and `rating` stay internal.
PUBLIC_EVENT_KINDS = (
    "status",
    "note",
    "kit_regenerated",
    "kit_edit",
    "followed_up",
    "scored",
    "rank",
)
ACT_NOW_STATUSES = ("new", "reviewing")
ARCHIVE_STATUS = "closed"
ARCHIVE_BATCH = 200


@dataclass(frozen=True, kw_only=True)
class RoleDraft:
    title: str
    source: str = "manual"
    url: str | None = None
    company: str | None = None
    location: str | None = None
    work_mode: str | None = None
    pay: str | None = None
    posted_at: datetime | None = None
    description: str | None = None
    status: str = "new"
    notes: str | None = None


@dataclass(frozen=True, kw_only=True)
class RejectionInput:
    rejected_at: datetime | None = None
    channel: str | None = None
    reason_categories: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class StatusChange:
    status: str
    version: int
    rank: str | None = None
    rejection: RejectionInput | None = None


class RoleService:
    def __init__(
        self,
        roles: RoleStore,
        guard: ContentGuard,
        clock: Clock,
        ids: Ids,
        prefs: PrefsSource | None = None,
    ) -> None:
        self._prefs = prefs
        self._roles = roles
        self._guard = guard
        self._clock = clock
        self._ids = ids

    async def list_roles(
        self, principal: Principal, query: RoleQuery, cursor: str | None, limit: int
    ) -> Page[RoleRecord]:
        offset = decode_cursor(cursor)
        query = self._with_age_bounds(query)
        rows = await self._roles.list_roles(principal.user_id, query, offset, limit + 1)
        return to_page(rows, offset, limit)

    def _with_age_bounds(self, query: RoleQuery) -> RoleQuery:
        """Resolve `min_age_days` / `max_age_days` into time bounds. `max_age_days=7` keeps roles
        seen in the last 7 days (inclusive); `min_age_days=14` keeps roles older than 14 days."""
        low, high = query.min_age_days, query.max_age_days
        if low is None and high is None:
            return query
        if low is not None and high is not None and low > high:
            raise ValidationFailed(
                "The minimum age must not be above the maximum.",
                errors=[FieldError("/min_age_days", "Must not be above max_age_days.")],
            )
        now = self._clock.now()
        return replace(
            query,
            seen_from=now - timedelta(days=high) if high is not None else None,
            seen_before=now - timedelta(days=low) if low is not None else None,
        )

    async def get_role(self, principal: Principal, role_id: uuid.UUID) -> RoleRecord:
        role = await self._roles.get_role(principal.user_id, role_id)
        if role is None:
            raise NotFound("That role doesn't exist.")
        return role

    async def create_role(self, principal: Principal, draft: RoleDraft) -> RoleRecord:
        if draft.description:
            # Fail closed: a pasted ad that the guard blocks is never stored or scored.
            verdict = await self._guard.inspect_input(draft.description)
            if verdict.is_blocked:
                raise GuardBlocked("This ad text can't be imported.")
        now = self._clock.now()
        role_id = self._ids.new_id()
        rank = await self._top_rank(principal.user_id, draft.status, exclude=None)
        values: dict[str, object] = {
            "id": role_id,
            "source": draft.source,
            "external_id": f"{draft.source}-{role_id.hex}",
            "title": draft.title,
            "url": draft.url,
            "company": draft.company,
            "location": draft.location,
            "work_mode": draft.work_mode,
            "pay": draft.pay,
            "posted_at": draft.posted_at,
            "description": draft.description,
            "status": draft.status,
            "stage": "new",
            "rank": rank,
            "notes": draft.notes,
            "found_at": now,
            "applied_at": now if draft.status == "applied" else None,
            "created_at": now,
            "updated_at": now,
        }
        event = self._event(now, "status", None, draft.status)
        return await self._roles.insert_role(principal.user_id, values, (event,))

    async def update_role(
        self, principal: Principal, role_id: uuid.UUID, version: int, changes: Mapping[str, object]
    ) -> RoleRecord:
        _check_changes(changes)
        now = self._clock.now()
        current = await self.get_role(principal, role_id)
        events: list[NewRoleEvent] = []
        if "notes" in changes and changes["notes"] != current.notes:
            events.append(self._event(now, "note"))
        if changes.get("followed_up_at") and current.followed_up_at is None:
            events.append(self._event(now, "followed_up"))
        values = {**changes, "updated_at": now}
        change = RoleWrite(role_id=role_id, version=version, values=values, events=tuple(events))
        return await self._roles.write_role(principal.user_id, change)

    async def delete_role(self, principal: Principal, role_id: uuid.UUID, version: int) -> None:
        now = self._clock.now()
        values = {"deleted_at": now, "updated_at": now}
        await self._roles.write_role(
            principal.user_id, RoleWrite(role_id=role_id, version=version, values=values)
        )

    async def change_status(
        self, principal: Principal, role_id: uuid.UUID, change: StatusChange
    ) -> RoleRecord:
        current = await self.get_role(principal, role_id)
        if change.rejection is not None and change.status != "rejected":
            raise ValidationFailed(
                "Rejection details only go with the rejected status.",
                errors=[FieldError("/rejection", "Only allowed with status rejected.")],
            )
        now = self._clock.now()
        rank = change.rank or await self._top_rank(principal.user_id, change.status, role_id)
        values: dict[str, object] = {"status": change.status, "rank": rank, "updated_at": now}
        if change.status == "applied" and current.applied_at is None:
            values["applied_at"] = now
        events: tuple[NewRoleEvent, ...] = ()
        if change.status != current.status:
            events = (self._event(now, "status", current.status, change.status),)
        rejection = None
        if change.status == "rejected":
            rejection = self._rejection(current, change.rejection, now)
        write = RoleWrite(
            role_id=role_id,
            version=change.version,
            values=values,
            events=events,
            rejection=rejection,
        )
        return await self._roles.write_role(principal.user_id, write)

    async def archive_older(self, principal: Principal, days: int | None = None) -> int:
        """Act now roles (new or reviewing, not filtered) posted more than `days` days ago, or
        found then when the post date is unknown, move to `closed`, one versioned write each.
        Without `days`, the user's `archive_default_days` setting (default 14) applies."""
        if days is None:
            days = (await prefs_for(self._prefs, principal.user_id)).archive_default_days
        if not 1 <= days <= 3650:
            raise ValidationFailed(
                "Pick a number of days between 1 and 3650.",
                errors=[FieldError("/days", "Must be between 1 and 3650.")],
            )
        now = self._clock.now()
        cutoff = now - timedelta(days=days)
        query = RoleQuery(statuses=ACT_NOW_STATUSES, sort="rank")
        stale: list[RoleRecord] = []
        offset = 0
        while True:
            rows = await self._roles.list_roles(principal.user_id, query, offset, ARCHIVE_BATCH)
            stale += [
                role
                for role in rows
                if role.stage != "filtered" and (role.posted_at or role.found_at) < cutoff
            ]
            if len(rows) < ARCHIVE_BATCH:
                break
            offset += ARCHIVE_BATCH
        for role in stale:
            rank = await self._top_rank(principal.user_id, ARCHIVE_STATUS, role.id)
            event = self._event(now, "status", role.status, ARCHIVE_STATUS)
            write = RoleWrite(
                role_id=role.id,
                version=role.version,
                values={"status": ARCHIVE_STATUS, "rank": rank, "updated_at": now},
                events=(event,),
            )
            await self._roles.write_role(principal.user_id, write)
        return len(stale)

    async def reorder(
        self, principal: Principal, role_id: uuid.UUID, rank: str, version: int
    ) -> RoleRecord:
        current = await self.get_role(principal, role_id)
        now = self._clock.now()
        events = (self._event(now, "rank", current.rank, rank),) if rank != current.rank else ()
        write = RoleWrite(
            role_id=role_id,
            version=version,
            values={"rank": rank, "updated_at": now},
            events=events,
        )
        return await self._roles.write_role(principal.user_id, write)

    async def list_events(
        self, principal: Principal, role_id: uuid.UUID, cursor: str | None, limit: int
    ) -> Page[RoleEventRecord]:
        await self.get_role(principal, role_id)
        offset = decode_cursor(cursor)
        rows = await self._roles.list_events(
            principal.user_id, role_id, PUBLIC_EVENT_KINDS, offset, limit + 1
        )
        return to_page(rows, offset, limit)

    async def record_event(
        self, principal: Principal, role_id: uuid.UUID, kind: str, to_value: str | None = None
    ) -> None:
        """Append a history event without touching the role's columns or `version`."""
        event = self._event(self._clock.now(), kind, None, to_value)
        write = RoleWrite(role_id=role_id, version=None, values={}, events=(event,))
        await self._roles.write_role(principal.user_id, write)

    async def _top_rank(self, user_id: uuid.UUID, status: str, exclude: uuid.UUID | None) -> str:
        """A key above everything in the column; rebalances the column when keys run out."""
        ranks = await self._roles.column_ranks(user_id, status)
        column = [(row_id, rank) for row_id, rank in ranks if row_id != exclude]
        first = column[0][1] if column else None
        key = key_between(None, first)
        if key is not None and len(key) <= MAX_RANK_CHARS:
            return key
        keys = spread(len(column) + 1)
        await self._roles.set_ranks(
            user_id, {row_id: keys[index + 1] for index, (row_id, _) in enumerate(column)}
        )
        return keys[0]

    def _event(
        self, at: datetime, kind: str, from_value: str | None = None, to_value: str | None = None
    ) -> NewRoleEvent:
        return NewRoleEvent(
            id=self._ids.new_id(), at=at, kind=kind, from_value=from_value, to_value=to_value
        )

    def _rejection(
        self, role: RoleRecord, given: RejectionInput | None, now: datetime
    ) -> dict[str, object]:
        details = given or RejectionInput()
        return {
            "id": self._ids.new_id(),
            "stage": role.status,
            "rejected_at": details.rejected_at or now,
            "channel": details.channel,
            "reason_categories": list(details.reason_categories),
            "created_at": now,
            "updated_at": now,
        }


def _check_changes(changes: Mapping[str, object]) -> None:
    if not changes:
        raise ValidationFailed("Change at least one field.")
    errors = [
        FieldError(f"/{name}", "This field can't be changed here.")
        for name in changes
        if name not in EDITABLE_FIELDS
    ]
    errors += [
        FieldError(f"/{name}", "This field can't be empty.")
        for name in REQUIRED_FIELDS
        if name in changes and not changes[name]
    ]
    if errors:
        raise ValidationFailed("Check the highlighted fields.", errors=errors)
