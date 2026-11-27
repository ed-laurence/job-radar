"""Postgres adapters for contacts, targets, chats, follow-ups, research runs and the
candidate profile, all under `user_transaction` (ARCH-05, SEC-03)."""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, cast

from sqlalchemy import ColumnElement, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.adapters.db.engine import user_transaction
from app.adapters.db.tables_board import RunStep
from app.adapters.db.tables_jobs import (
    Contact,
    Experience,
    ExperienceEntry,
    Followup,
    Rejection,
    RoleChat,
    Target,
)
from app.adapters.db.tables_platform import UserSettings
from app.adapters.db.tables_research import ResearchRun
from app.domain.errors import FieldError, NotFound, ValidationFailed
from app.domain.jobs.experience import EntryText, profile_item
from app.domain.jobs.ports import TextSearch, Values
from app.domain.jobs.records import (
    CandidateProfile,
    ChatRecord,
    ExperienceItem,
    FollowupRecord,
    ResearchRunRecord,
    StepRecord,
)

if TYPE_CHECKING:
    from _typeshed import DataclassInstance
from app.adapters.db.jobs_mapping import (
    UNIQUE_VIOLATION,
    URL_PLACEHOLDER,
    Sessions,
    diagnose_missing,
    to_record,
)

LIKE_ESCAPE = "\\"


def like_escape(term: str) -> str:
    """`term` as a literal inside a LIKE pattern: the escape character, % and _ escaped."""
    for char in (LIKE_ESCAPE, "%", "_"):
        term = term.replace(char, LIKE_ESCAPE + char)
    return term


class PgRows[R: DataclassInstance]:
    """Contacts or targets: soft delete, `version`, and a per-user natural key."""

    def __init__(
        self,
        sessions: Sessions,
        orm: type[Contact] | type[Target],
        record_type: type[R],
        unique: str,
    ) -> None:
        self._sessions = sessions
        self._orm = orm
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
        orm = self._orm
        conditions = [
            column.in_(value) if isinstance(value, tuple) else column == value
            for column, value in ((getattr(orm, name), value) for name, value in filters.items())
        ]
        if search is not None:
            conditions.append(self._matches(search))
        stmt = (
            select(orm)
            .where(orm.user_id == user_id, orm.deleted_at.is_(None), *conditions)
            .order_by(orm.created_at.desc(), orm.id.desc())
            .offset(offset)
            .limit(limit)
        )
        async with user_transaction(self._sessions, user_id) as session:
            return [to_record(self._type, row) for row in await session.scalars(stmt)]

    def _matches(self, search: TextSearch) -> ColumnElement[bool]:
        pattern = f"%{like_escape(search.term)}%"
        texts: list[ColumnElement[str]] = [getattr(self._orm, name) for name in search.columns]
        if search.full_name:
            texts.append(func.concat_ws(" ", Contact.first_name, Contact.last_name))
        return or_(*(text.ilike(pattern, escape=LIKE_ESCAPE) for text in texts))

    async def get_row(self, user_id: uuid.UUID, row_id: uuid.UUID) -> R | None:
        async with user_transaction(self._sessions, user_id) as session:
            row = await session.get(self._orm, row_id)
            if row is None or row.deleted_at is not None:
                return None
            return to_record(self._type, row)

    async def insert_row(self, user_id: uuid.UUID, values: Values) -> R:
        stored = self._with_placeholder(values, values.get("id"))
        try:
            async with user_transaction(self._sessions, user_id) as session:
                row = self._orm(user_id=user_id, **stored)
                session.add(row)
                await session.flush()
                await session.refresh(row)
                return to_record(self._type, row)
        except IntegrityError as error:
            raise self._duplicate(error) from None

    async def update_row(
        self, user_id: uuid.UUID, row_id: uuid.UUID, version: int, values: Values
    ) -> R:
        orm = self._orm
        stored = self._with_placeholder(values, row_id) if "url" in values else dict(values)
        stmt = (
            update(orm)
            .where(orm.id == row_id, orm.deleted_at.is_(None), orm.version == version)
            .values(**stored, version=orm.version + 1)
            .returning(orm)
        )
        try:
            async with user_transaction(self._sessions, user_id) as session:
                row = await session.scalar(stmt)
                if row is None:
                    await diagnose_missing(session, orm, row_id, "record")
                return to_record(self._type, row)
        except IntegrityError as error:
            raise self._duplicate(error) from None

    def _with_placeholder(self, values: Values, row_id: object) -> dict[str, object]:
        stored = dict(values)
        if self._unique == "url" and not stored.get("url"):
            stored["url"] = f"{URL_PLACEHOLDER}{row_id}"
        return stored

    def _duplicate(self, error: IntegrityError) -> Exception:
        code = getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)
        if code != UNIQUE_VIOLATION:
            return error
        pointer = f"/{self._unique}"
        return ValidationFailed(
            "That one already exists.", errors=[FieldError(pointer, "Already exists.")]
        )


class PgChats:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def list_messages(
        self, user_id: uuid.UUID, role_id: uuid.UUID, offset: int, limit: int
    ) -> list[ChatRecord]:
        stmt = (
            select(RoleChat)
            .where(RoleChat.role_id == role_id)
            .order_by(RoleChat.created_at, RoleChat.id)
            .offset(offset)
            .limit(limit)
        )
        async with user_transaction(self._sessions, user_id) as session:
            return [to_record(ChatRecord, row) for row in await session.scalars(stmt)]

    async def recent_messages(
        self, user_id: uuid.UUID, role_id: uuid.UUID, limit: int
    ) -> list[ChatRecord]:
        stmt = (
            select(RoleChat)
            .where(RoleChat.role_id == role_id)
            .order_by(RoleChat.created_at.desc(), RoleChat.id.desc())
            .limit(limit)
        )
        async with user_transaction(self._sessions, user_id) as session:
            rows = [to_record(ChatRecord, row) for row in await session.scalars(stmt)]
        return rows[::-1]

    async def add_message(self, user_id: uuid.UUID, message: ChatRecord) -> None:
        async with user_transaction(self._sessions, user_id) as session:
            session.add(RoleChat(user_id=user_id, **dataclasses.asdict(message)))

    async def clear(self, user_id: uuid.UUID, role_id: uuid.UUID) -> None:
        async with user_transaction(self._sessions, user_id) as session:
            await session.execute(delete(RoleChat).where(RoleChat.role_id == role_id))


class PgFollowups:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def replace_suggestions(
        self, user_id: uuid.UUID, role_id: uuid.UUID, rows: Sequence[Values]
    ) -> list[FollowupRecord]:
        async with user_transaction(self._sessions, user_id) as session:
            await session.execute(
                delete(Followup).where(Followup.role_id == role_id, Followup.status == "suggested")
            )
            created = [Followup(user_id=user_id, role_id=role_id, **row) for row in rows]
            session.add_all(created)
            await session.flush()
            for row in created:
                await session.refresh(row)
            return [to_record(FollowupRecord, row) for row in created]


class PgResearch:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def create_run(self, user_id: uuid.UUID, values: Values) -> ResearchRunRecord:
        async with user_transaction(self._sessions, user_id) as session:
            row = ResearchRun(user_id=user_id, **values)
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return to_record(ResearchRunRecord, row)

    async def get_run(self, user_id: uuid.UUID, run_id: uuid.UUID) -> ResearchRunRecord | None:
        async with user_transaction(self._sessions, user_id) as session:
            row = await session.get(ResearchRun, run_id)
            return to_record(ResearchRunRecord, row) if row is not None else None

    async def update_run(
        self, user_id: uuid.UUID, run_id: uuid.UUID, values: Values
    ) -> ResearchRunRecord:
        async with user_transaction(self._sessions, user_id) as session:
            stmt = update(ResearchRun).where(ResearchRun.id == run_id)
            row = await session.scalar(stmt.values(**values).returning(ResearchRun))
            if row is None:
                raise NotFound("That research run doesn't exist.")
            return to_record(ResearchRunRecord, row)

    async def add_step(self, user_id: uuid.UUID, step: StepRecord) -> None:
        """Research steps share `run_steps` with the board (one step stream); the row is owned
        by `user_id`, so RLS keeps it private, and the insert notifies `run_steps`."""
        values = dataclasses.asdict(step)
        values["research_run_id"] = values.pop("run_id")
        async with user_transaction(self._sessions, user_id) as session:
            session.add(RunStep(user_id=user_id, **values))

    async def steps_after(
        self, user_id: uuid.UUID, run_id: uuid.UUID, seq: int
    ) -> list[StepRecord]:
        stmt = (
            select(RunStep)
            .where(RunStep.research_run_id == run_id, RunStep.seq > seq)
            .order_by(RunStep.seq)
        )
        async with user_transaction(self._sessions, user_id) as session:
            rows = await session.scalars(stmt)
            return [to_record(StepRecord, row, run_id=run_id) for row in rows]


class PgProfiles:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def load_profile(self, user_id: uuid.UUID) -> CandidateProfile:
        async with user_transaction(self._sessions, user_id) as session:
            settings = await session.get(UserSettings, user_id)
            entries = await session.execute(
                select(
                    Experience.id,
                    Experience.requirement,
                    ExperienceEntry.context,
                    ExperienceEntry.detail,
                )
                .join(ExperienceEntry, ExperienceEntry.experience_id == Experience.id, isouter=True)
                .order_by(
                    Experience.created_at,
                    Experience.id,
                    ExperienceEntry.created_at,
                    ExperienceEntry.id,
                )
            )
            lessons = await session.scalars(
                select(Rejection.lesson)
                .where(Rejection.use_in_kits.is_(True), Rejection.lesson.is_not(None))
                .order_by(Rejection.updated_at.desc())
                .limit(8)
            )
            grouped = _group_experience((row[0], row[1], row[2], row[3]) for row in entries.all())
            raw = (settings.settings if settings else {}).get("profile")
            profile = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
            return CandidateProfile(
                name=_text(profile.get("name")),
                headline=_text(profile.get("headline")),
                location=_text(profile.get("location")),
                email=_text(profile.get("email")),
                country=_text(profile.get("country")) or "Australia",
                spelling=_text(profile.get("spelling")) or "en-AU",
                experience=grouped,
                lessons=tuple(lesson for lesson in lessons if lesson),
            )


def _group_experience(
    rows: Iterable[tuple[uuid.UUID, str, str | None, str | None]],
) -> tuple[ExperienceItem, ...]:
    """One item per record (oldest first): its requirement and one detail per entry."""
    records: dict[uuid.UUID, tuple[str, list[EntryText]]] = {}
    for experience_id, requirement, context, detail in rows:
        requirement_entries = records.setdefault(experience_id, (requirement, []))
        if detail:
            requirement_entries[1].append(EntryText(context=context or "", detail=detail))
    return tuple(profile_item(requirement, entries) for requirement, entries in records.values())


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
