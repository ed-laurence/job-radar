"""In-memory fakes for every job-search port (ARCH-04): unit tests and offline dev without a
database. Semantics mirror the Postgres adapter, including per-user isolation and sort order."""

from __future__ import annotations

import dataclasses
import json
import uuid
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Protocol, cast

from app.adapters.fakes.devboard import Publisher
from app.domain.errors import Conflict, FieldError, NotFound, ValidationFailed
from app.domain.jobs.experience import ExperienceEntryRecord, as_profile_item
from app.domain.jobs.ports import (
    NewRoleEvent,
    RoleQuery,
    RoleWrite,
    TextSearch,
    Values,
)
from app.domain.jobs.records import (
    CandidateProfile,
    ChatRecord,
    FollowupRecord,
    KitAnswerRecord,
    KitRecord,
    RejectionRecord,
    ResearchRunRecord,
    RoleEventRecord,
    RoleRecord,
    StepRecord,
)
from app.domain.steps import RUN_STEPS_CHANNEL

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

type Key = tuple[uuid.UUID, uuid.UUID]


def _freeze(value: object) -> object:
    return tuple(cast("list[object]", value)) if isinstance(value, list) else value


def build[R: DataclassInstance](record_type: type[R], values: Values) -> R:
    names = {item.name for item in dataclasses.fields(record_type)}
    return record_type(**{name: _freeze(value) for name, value in values.items() if name in names})


def patch[R: DataclassInstance](record: R, values: Values) -> R:
    names = {item.name for item in dataclasses.fields(record)}
    return dataclasses.replace(
        record, **{name: _freeze(value) for name, value in values.items() if name in names}
    )


def _sort_key(role: RoleRecord, sort: str) -> tuple[object, ...]:
    if sort == "-found_at":
        return (-role.found_at.timestamp(), role.id)
    if sort == "-score":
        return (role.score is None, -(role.score or 0), role.id)
    if sort == "-posted_at":
        posted = role.posted_at.timestamp() if role.posted_at else 0.0
        return (role.posted_at is None, -posted, role.id)
    return (role.rank, role.id)


def _matches(role: RoleRecord, query: RoleQuery) -> bool:
    text = (query.text or "").lower()
    return (
        role.deleted_at is None
        and (not query.statuses or role.status in query.statuses)
        and (not query.stages or role.stage in query.stages)
        and (query.track is None or role.track == query.track)
        and (query.min_score is None or (role.score or -1) >= query.min_score)
        and (not text or text in role.title.lower() or text in (role.company or "").lower())
        and _within_age(role, query)
    )


def _within_age(role: RoleRecord, query: RoleQuery) -> bool:
    seen = role.posted_at or role.found_at
    if query.seen_from is not None and seen < query.seen_from:
        return False
    return query.seen_before is None or seen < query.seen_before


class MemoryRoles:
    def __init__(self) -> None:
        self.roles: dict[Key, RoleRecord] = {}
        self.events: dict[Key, RoleEventRecord] = {}
        self.kits: dict[Key, KitRecord] = {}
        self.rejections: dict[Key, RejectionRecord] = {}

    async def list_roles(
        self, user_id: uuid.UUID, query: RoleQuery, offset: int, limit: int
    ) -> list[RoleRecord]:
        rows = [role for role in self._owned(user_id) if _matches(role, query)]
        rows.sort(key=lambda role: _sort_key(role, query.sort))
        return rows[offset : offset + limit]

    async def get_role(self, user_id: uuid.UUID, role_id: uuid.UUID) -> RoleRecord | None:
        role = self.roles.get((user_id, role_id))
        if role is None or role.deleted_at is not None:
            return None
        return dataclasses.replace(role, kit=self.kits.get((user_id, role_id)))

    async def insert_role(
        self, user_id: uuid.UUID, values: Values, events: Sequence[NewRoleEvent]
    ) -> RoleRecord:
        role = build(RoleRecord, {"version": 1, **values})
        self.roles[(user_id, role.id)] = role
        self._add_events(user_id, role.id, events)
        return role

    async def write_role(self, user_id: uuid.UUID, change: RoleWrite) -> RoleRecord:
        current = self.roles.get((user_id, change.role_id))
        if current is None or current.deleted_at is not None:
            raise NotFound("That role doesn't exist.")
        if change.version is not None and change.version != current.version:
            raise Conflict("The role changed since you read it.", current_version=current.version)
        bump = 1 if change.values else 0
        updated = patch(current, {**change.values, "version": current.version + bump})
        self.roles[(user_id, change.role_id)] = updated
        self._add_events(user_id, change.role_id, change.events)
        if change.rejection is not None:
            self._upsert_rejection(user_id, change.role_id, change.rejection)
        return dataclasses.replace(updated, kit=self.kits.get((user_id, change.role_id)))

    async def column_ranks(self, user_id: uuid.UUID, status: str) -> list[tuple[uuid.UUID, str]]:
        rows = [r for r in self._owned(user_id) if r.status == status and r.deleted_at is None]
        rows.sort(key=lambda role: (role.rank, role.id))
        return [(role.id, role.rank) for role in rows]

    async def set_ranks(self, user_id: uuid.UUID, ranks: Mapping[uuid.UUID, str]) -> None:
        for role_id, rank in ranks.items():
            key = (user_id, role_id)
            self.roles[key] = dataclasses.replace(self.roles[key], rank=rank)

    async def list_events(
        self,
        user_id: uuid.UUID,
        role_id: uuid.UUID,
        kinds: Sequence[str],
        offset: int,
        limit: int,
    ) -> list[RoleEventRecord]:
        rows = [e for (owner, _), e in self.events.items() if owner == user_id]
        rows = [event for event in rows if event.role_id == role_id and event.kind in kinds]
        rows.sort(key=lambda event: (event.at, event.id), reverse=True)
        return rows[offset : offset + limit]

    async def save_kit(self, user_id: uuid.UUID, role_id: uuid.UUID, values: Values) -> KitRecord:
        current = self.kits.get((user_id, role_id))
        answers = tuple(
            KitAnswerRecord(q=str(item["q"]), a=str(item["a"]))
            for item in cast("list[dict[str, str]]", values.get("answers") or [])
        )
        merged = {**values, "answers": answers, "role_id": role_id}
        kit = build(KitRecord, {**merged, "version": (current.version + 1) if current else 1})
        self.kits[(user_id, role_id)] = kit
        return kit

    async def get_rejection(self, user_id: uuid.UUID, role_id: uuid.UUID) -> RejectionRecord | None:
        return self.rejections.get((user_id, role_id))

    async def update_rejection(
        self, user_id: uuid.UUID, role_id: uuid.UUID, values: Values
    ) -> RejectionRecord:
        current = self.rejections.get((user_id, role_id))
        if current is None:
            raise NotFound("That role has no rejection recorded.")
        updated = patch(current, {**values, "version": current.version + 1})
        self.rejections[(user_id, role_id)] = updated
        return updated

    async def list_rejections(self, user_id: uuid.UUID, limit: int) -> list[RejectionRecord]:
        live = {role.id for role in self._owned(user_id) if role.deleted_at is None}
        rows = [r for (owner, _), r in self.rejections.items() if owner == user_id]
        rows = [r for r in rows if r.role_id in live]
        rows.sort(key=lambda r: (r.rejected_at is not None, r.rejected_at), reverse=True)
        return rows[:limit]

    def _owned(self, user_id: uuid.UUID) -> list[RoleRecord]:
        return [role for (owner, _), role in self.roles.items() if owner == user_id]

    def _add_events(
        self, user_id: uuid.UUID, role_id: uuid.UUID, events: Sequence[NewRoleEvent]
    ) -> None:
        for event in events:
            record = build(RoleEventRecord, {**dataclasses.asdict(event), "role_id": role_id})
            self.events[(user_id, event.id)] = record

    def _upsert_rejection(self, user_id: uuid.UUID, role_id: uuid.UUID, values: Values) -> None:
        current = self.rejections.get((user_id, role_id))
        if current is None:
            record = build(RejectionRecord, {"version": 1, **values, "role_id": role_id})
        else:
            update = {k: v for k, v in values.items() if k not in {"id", "created_at"}}
            record = patch(current, {**update, "version": current.version + 1})
        self.rejections[(user_id, role_id)] = record


class MemoryRows[R: DataclassInstance]:
    """Contacts or targets. `unique` names the natural key column (contact URL, target name)."""

    def __init__(self, record_type: type[R], unique: str) -> None:
        self.rows: dict[Key, R] = {}
        self._type = record_type
        self._unique = unique

    async def list_rows(
        self,
        user_id: uuid.UUID,
        filters: Values,
        offset: int,
        limit: int,
        search: TextSearch | None = None,
    ) -> list[R]:
        rows = [row for row in self._live(user_id) if _filtered(row, filters)]
        if search is not None:
            rows = [row for row in rows if _searched(row, search)]
        rows.sort(key=lambda row: (_attr(row, "created_at"), str(_attr(row, "id"))), reverse=True)
        return rows[offset : offset + limit]

    async def get_row(self, user_id: uuid.UUID, row_id: uuid.UUID) -> R | None:
        row = self.rows.get((user_id, row_id))
        return row if row is not None and _attr(row, "deleted_at") is None else None

    async def insert_row(self, user_id: uuid.UUID, values: Values) -> R:
        self._check_unique(user_id, None, values)
        row = build(self._type, {"version": 1, **values})
        self.rows[(user_id, cast("uuid.UUID", _attr(row, "id")))] = row
        return row

    async def update_row(
        self, user_id: uuid.UUID, row_id: uuid.UUID, version: int, values: Values
    ) -> R:
        current = await self.get_row(user_id, row_id)
        if current is None:
            raise NotFound("That record doesn't exist.")
        current_version = cast("int", _attr(current, "version"))
        if current_version != version:
            raise Conflict("It changed since you read it.", current_version=current_version)
        self._check_unique(user_id, row_id, values)
        updated = patch(current, {**values, "version": current_version + 1})
        self.rows[(user_id, row_id)] = updated
        return updated

    def _live(self, user_id: uuid.UUID) -> list[R]:
        return [
            row
            for (owner, _), row in self.rows.items()
            if owner == user_id and _attr(row, "deleted_at") is None
        ]

    def _check_unique(self, user_id: uuid.UUID, row_id: uuid.UUID | None, values: Values) -> None:
        column = "name_key" if self._unique == "name" else self._unique
        value = values.get(column)
        if value is None:
            return
        for (owner, other_id), row in self.rows.items():
            if owner == user_id and other_id != row_id and _attr(row, column) == value:
                pointer = f"/{self._unique}"
                raise ValidationFailed(
                    "That one already exists.", errors=[FieldError(pointer, "Already exists.")]
                )


def _attr(row: object, name: str) -> object:
    return getattr(row, name, None)


def _searched(row: object, search: TextSearch) -> bool:
    texts = [_attr(row, name) for name in search.columns]
    if search.full_name:
        names = (_attr(row, "first_name"), _attr(row, "last_name"))
        texts.append(" ".join(str(name) for name in names if name))
    wanted = search.term.casefold()
    return any(isinstance(text, str) and wanted in text.casefold() for text in texts)


def _filtered(row: object, filters: Values) -> bool:
    return all(
        _attr(row, name) in value if isinstance(value, tuple) else _attr(row, name) == value
        for name, value in filters.items()
    )


class MemoryChats:
    def __init__(self) -> None:
        self.messages: list[tuple[uuid.UUID, ChatRecord]] = []

    async def list_messages(
        self, user_id: uuid.UUID, role_id: uuid.UUID, offset: int, limit: int
    ) -> list[ChatRecord]:
        return self._for(user_id, role_id)[offset : offset + limit]

    async def recent_messages(
        self, user_id: uuid.UUID, role_id: uuid.UUID, limit: int
    ) -> list[ChatRecord]:
        return self._for(user_id, role_id)[-limit:]

    async def add_message(self, user_id: uuid.UUID, message: ChatRecord) -> None:
        self.messages.append((user_id, message))

    async def clear(self, user_id: uuid.UUID, role_id: uuid.UUID) -> None:
        self.messages = [
            (owner, m)
            for owner, m in self.messages
            if not (owner == user_id and m.role_id == role_id)
        ]

    def _for(self, user_id: uuid.UUID, role_id: uuid.UUID) -> list[ChatRecord]:
        rows = [m for owner, m in self.messages if owner == user_id and m.role_id == role_id]
        return sorted(rows, key=lambda message: (message.created_at, message.id))


class MemoryFollowups:
    def __init__(self) -> None:
        self.rows: dict[Key, FollowupRecord] = {}

    async def replace_suggestions(
        self, user_id: uuid.UUID, role_id: uuid.UUID, rows: Sequence[Values]
    ) -> list[FollowupRecord]:
        self.rows = {
            key: row
            for key, row in self.rows.items()
            if not (key[0] == user_id and row.role_id == role_id and row.status == "suggested")
        }
        created = [build(FollowupRecord, {"version": 1, **row, "role_id": role_id}) for row in rows]
        for record in created:
            self.rows[(user_id, record.id)] = record
        return created


class MemoryResearch:
    def __init__(self, publisher: Publisher | None = None) -> None:
        self.runs: dict[Key, ResearchRunRecord] = {}
        self.steps: list[tuple[uuid.UUID, StepRecord]] = []
        self._publisher = publisher

    async def create_run(self, user_id: uuid.UUID, values: Values) -> ResearchRunRecord:
        run = build(ResearchRunRecord, values)
        self.runs[(user_id, run.id)] = run
        return run

    async def get_run(self, user_id: uuid.UUID, run_id: uuid.UUID) -> ResearchRunRecord | None:
        return self.runs.get((user_id, run_id))

    async def update_run(
        self, user_id: uuid.UUID, run_id: uuid.UUID, values: Values
    ) -> ResearchRunRecord:
        run = patch(self.runs[(user_id, run_id)], values)
        self.runs[(user_id, run_id)] = run
        return run

    async def add_step(self, user_id: uuid.UUID, step: StepRecord) -> None:
        self.steps.append((user_id, step))
        if self._publisher is not None:
            # Like the `run_steps` NOTIFY trigger: ids only.
            payload = {"run_id": str(step.run_id), "seq": step.seq, "id": str(step.id)}
            await self._publisher.publish(RUN_STEPS_CHANNEL, json.dumps(payload))

    async def steps_after(
        self, user_id: uuid.UUID, run_id: uuid.UUID, seq: int
    ) -> list[StepRecord]:
        rows = [s for owner, s in self.steps if owner == user_id and s.run_id == run_id]
        return sorted((s for s in rows if s.seq > seq), key=lambda step: step.seq)


class ExperienceSource(Protocol):
    def owned(self, user_id: uuid.UUID) -> list[ExperienceEntryRecord]: ...


class MemoryProfiles:
    def __init__(self, experience: ExperienceSource | None = None) -> None:
        self.profiles: dict[uuid.UUID, CandidateProfile] = {}
        self._experience = experience

    async def load_profile(self, user_id: uuid.UUID) -> CandidateProfile:
        profile = self.profiles.get(user_id, CandidateProfile())
        records = self._experience.owned(user_id) if self._experience is not None else []
        if not records:
            return profile
        items = tuple(as_profile_item(record) for record in records)
        return dataclasses.replace(profile, experience=(*profile.experience, *items))
