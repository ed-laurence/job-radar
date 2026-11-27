"""Experience records, the LinkedIn import, local document storage and the roles age filter
against Postgres under row-level security."""

from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.jobs_roles import PgRoles
from app.adapters.db.jobs_stores import PgProfiles, PgRows
from app.adapters.db.profile_stores import PgContactImport, PgExperience
from app.adapters.db.tables_jobs import Contact
from app.adapters.fakes.security import FakeContentGuard, FakePII
from app.adapters.fakes.system import FakeClock, SequentialIds
from app.adapters.pdf import PypdfText
from app.adapters.storage_local import LocalStorage
from app.domain.errors import Conflict, NotFound
from app.domain.identity import AppRole, Principal
from app.domain.jobs.documents import ImportSummary, ProfileDocumentService
from app.domain.jobs.experience import EntryText, ExperienceDraft, ExperienceService
from app.domain.jobs.linkedin import LinkedinImportService
from app.domain.jobs.ports import RoleQuery
from app.domain.jobs.records import ContactRecord
from app.domain.jobs.roles import RoleDraft, RoleService
from tests.pdfs import b64, make_pdf

pytestmark = pytest.mark.integration

type Sessions = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)


def _principal(user_id: uuid.UUID) -> Principal:
    return Principal(user_id=user_id, email="x@example.test", role=AppRole.USER)


def _ids() -> SequentialIds:
    return SequentialIds(start=uuid.uuid4().int % 10**9)


async def test_experience_records_under_rls(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    alice, bob = (_principal(user) for user in two_users)
    service = ExperienceService(PgExperience(sessions), FakeClock(NOW), _ids())
    draft = ExperienceDraft(
        requirement="Platform lead",
        entries=(
            EntryText(context="Platform lead, Example Co", detail="Ran the data platform."),
            EntryText(context="Other", detail="Cut costs by a third."),
        ),
    )
    record = await service.create(alice, draft)
    assert [r.id for r in await service.list_records(alice)] == [record.id]
    assert await service.list_records(bob) == []
    with pytest.raises(NotFound):
        await service.update(bob, record.id, 1, draft)
    assert [(e.ref, e.context) for e in record.entries] == [
        ("E1.1", "Platform lead, Example Co"),
        ("E1.2", "Other"),
    ]
    one_entry = (EntryText(context="Personal project", detail="Ran it."),)
    updated = await service.update(
        alice, record.id, 1, ExperienceDraft(requirement="Platform lead", entries=one_entry)
    )
    assert (updated.version, updated.ref) == (2, "E1")
    assert [(e.context, e.detail) for e in updated.entries] == [("Personal project", "Ran it.")]
    with pytest.raises(Conflict):
        await service.update(alice, record.id, 1, draft)

    profile = await PgProfiles(sessions).load_profile(alice.user_id)
    assert any(item.requirement == "Platform lead" for item in profile.experience)
    assert all(
        item.requirement != "Platform lead"
        for item in (await PgProfiles(sessions).load_profile(bob.user_id)).experience
    )

    with pytest.raises(NotFound):
        await service.delete(bob, record.id, 2)
    with pytest.raises(Conflict):
        await service.delete(alice, record.id, 1)
    await service.delete(alice, record.id, 2)
    assert await service.list_records(alice) == []


class _Summaries:
    def __init__(self) -> None:
        self.recorded: dict[uuid.UUID, ImportSummary] = {}

    async def record_import(self, user_id: uuid.UUID, summary: ImportSummary) -> None:
        self.recorded[user_id] = summary

    async def last_import(self, user_id: uuid.UUID) -> ImportSummary | None:
        return self.recorded.get(user_id)


CSV = (
    "Notes:\n\n"
    "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
    "Alex,Example,https://www.linkedin.com/in/alex-x,alex@example.test,Acme,Lead,26 Nov 2026\n"
    "=cmd,Example,https://www.linkedin.com/in/formula-x,,Widgets,+Boss,01 Jan 2025\n"
)


async def test_contacts_import_upsert_and_remove_all_under_rls(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    alice, bob = (_principal(user) for user in two_users)
    store = PgContactImport(sessions)
    service = LinkedinImportService(store, _Summaries(), FakeClock(NOW), _ids())
    data = base64.b64encode(CSV.encode()).decode()
    first = await service.import_csv(alice, "Connections.csv", data)
    assert (first.imported, first.updated, first.skipped) == (2, 0, 0)
    again = await service.import_csv(alice, "Connections.csv", data)
    assert (again.imported, again.updated) == (0, 2)
    other = await service.import_csv(bob, "Connections.csv", data)
    assert (other.imported, other.updated) == (2, 0)
    assert await store.count(alice.user_id) == 2

    rows = await PgRows(sessions, Contact, ContactRecord, "url").list_rows(alice.user_id, {}, 0, 10)
    by_url = {row.url: row for row in rows}
    formula = by_url["https://www.linkedin.com/in/formula-x"]
    assert (formula.first_name, formula.position) == ("'=cmd", "'+Boss")
    assert by_url["https://www.linkedin.com/in/alex-x"].connected_on == date(2026, 11, 26)

    assert await service.delete_all(alice, confirm=True) == 2
    assert await store.count(alice.user_id) == 0
    assert await store.count(bob.user_id) == 2
    await service.delete_all(bob, confirm=True)


def _files(root: Path) -> list[str]:
    return [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]


async def test_local_storage_documents_are_namespaced(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    alice, bob = _principal(uuid.uuid4()), _principal(uuid.uuid4())
    service = ProfileDocumentService(
        storage, PypdfText(), FakeContentGuard(), FakePII(), FakeClock(NOW), _ids()
    )
    info = await service.upload(alice, "technical", "cv.pdf", b64(make_pdf()))
    assert info.readability.ok
    assert (await service.download(alice, "technical")).data == make_pdf()
    with pytest.raises(NotFound):
        await service.download(bob, "technical")
    assert "technical" in await service.texts(alice.user_id)
    assert await service.texts(bob.user_id) == {}
    files = await asyncio.to_thread(_files, tmp_path)
    assert files
    assert all(name.startswith(f"users/{alice.user_id}/profile/") for name in files)
    await service.delete(alice, "technical")
    assert await service.list_documents(alice) == []
    with pytest.raises(ValueError, match="invalid storage key"):
        await storage.get("../outside")


async def test_roles_age_filter_in_postgres(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    alice, bob = (_principal(user) for user in two_users)
    clock = FakeClock(NOW)
    service = RoleService(PgRoles(sessions), FakeContentGuard(), clock, _ids())
    ages = {
        "posted-7": (NOW - timedelta(days=7), NOW),
        "posted-15": (NOW - timedelta(days=15), NOW),
        "found-20": (None, NOW - timedelta(days=20)),
        "found-1": (None, NOW - timedelta(days=1)),
        "posted-14": (NOW - timedelta(days=14), NOW),
    }
    for title, (posted, found) in ages.items():
        clock.set(found)
        await service.create_role(alice, RoleDraft(title=title, posted_at=posted))
    clock.set(NOW)

    async def titles(principal: Principal, query: RoleQuery) -> set[str]:
        return {r.title for r in (await service.list_roles(principal, query, None, 50)).items}

    assert await titles(alice, RoleQuery(max_age_days=7)) == {"posted-7", "found-1"}
    assert await titles(alice, RoleQuery(min_age_days=14)) == {"posted-15", "found-20"}
    assert await titles(alice, RoleQuery(min_age_days=7, max_age_days=14)) == {"posted-14"}
    assert await titles(bob, RoleQuery(max_age_days=365)) == set()
