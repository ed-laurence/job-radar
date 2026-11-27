"""Contact and target search (`q` on listContacts / listTargets) against Postgres: a
case-insensitive substring over the text columns, LIKE wildcards taken literally, combined with
the other filters and pagination, and never across tenants. Fictional names only."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.jobs_stores import PgRows
from app.adapters.db.tables_jobs import Contact, Target
from app.adapters.fakes.system import FakeClock, SequentialIds
from app.domain.identity import AppRole, Principal
from app.domain.jobs.crm import ContactService, TargetService
from app.domain.jobs.records import ContactRecord, TargetRecord

pytestmark = pytest.mark.integration

type Sessions = async_sessionmaker[AsyncSession]


def _principal(user_id: uuid.UUID) -> Principal:
    return Principal(user_id=user_id, email="x@example.test", role=AppRole.USER)


def _ids() -> SequentialIds:
    return SequentialIds(start=uuid.uuid4().int % 10**9)


def _contacts(sessions: Sessions) -> ContactService:
    return ContactService(PgRows(sessions, Contact, ContactRecord, "url"), FakeClock(), _ids())


def _targets(sessions: Sessions) -> TargetService:
    return TargetService(PgRows(sessions, Target, TargetRecord, "name"), FakeClock(), _ids())


async def _add(service: ContactService, who: Principal, **fields: str) -> ContactRecord:
    url = f"https://people.example.test/{uuid.uuid4().hex}"
    return await service.create_contact(who, {"url": url, **fields})


async def _found(
    service: ContactService,
    who: Principal,
    q: str | None,
    *,
    status: str | None = None,
    company: str | None = None,
) -> list[str]:
    page = await service.list_contacts(who, status, company, None, 50, q=q)
    return sorted(c.first_name or "" for c in page.items)


async def test_contact_search_matches_each_field_case_insensitively(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    alice = _principal(two_users[0])
    service = _contacts(sessions)
    await _add(service, alice, first_name="Riley", last_name="Tanaka", company="Northwind Labs")
    await _add(service, alice, first_name="Sam", last_name="Okafor", position="Data Lead")
    await _add(service, alice, first_name="Jo", last_name="Park", email="jo.park@example.test")
    assert await _found(service, alice, "riley") == ["Riley"]
    assert await _found(service, alice, "TANAKA") == ["Riley"]
    assert await _found(service, alice, "riley tanaka") == ["Riley"]  # full name
    assert await _found(service, alice, "  riley   tanaka ") == ["Riley"]  # whitespace collapsed
    assert await _found(service, alice, "northwind") == ["Riley"]
    assert await _found(service, alice, "data lead") == ["Sam"]
    assert await _found(service, alice, "jo.park@") == ["Jo"]
    assert await _found(service, alice, "nobody") == []
    assert await _found(service, alice, "   ") == ["Jo", "Riley", "Sam"]  # blank: no filter
    assert await _found(service, alice, None) == ["Jo", "Riley", "Sam"]


async def test_like_wildcards_are_literal(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    alice = _principal(two_users[0])
    service = _contacts(sessions)
    await _add(service, alice, first_name="Pct", company="100% Data")
    await _add(service, alice, first_name="Under", company="data_works")
    await _add(service, alice, first_name="Slash", company="A\\B Analytics")
    await _add(service, alice, first_name="Plain", company="Datax Works")
    assert await _found(service, alice, "%") == ["Pct"]
    assert await _found(service, alice, "a_w") == ["Under"]
    assert await _found(service, alice, "\\") == ["Slash"]
    assert await _found(service, alice, "data") == ["Pct", "Plain", "Under"]


async def test_search_combines_with_filters_and_pagination(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    alice = _principal(two_users[0])
    service = _contacts(sessions)
    for n in range(5):
        await _add(service, alice, first_name=f"Lee{n}", company="Fabrikam")
    await _add(service, alice, first_name="Leeway", company="Contoso")
    await _add(service, alice, first_name="Other", company="Fabrikam")
    assert len(await _found(service, alice, "lee")) == 6
    assert len(await _found(service, alice, "lee", company="Fabrikam")) == 5
    assert await _found(service, alice, "lee", status="sent") == []

    first = await service.list_contacts(alice, None, "Fabrikam", None, 3, q="lee")
    assert len(first.items) == 3
    assert first.next_cursor is not None
    rest = await service.list_contacts(alice, None, "Fabrikam", first.next_cursor, 3, q="lee")
    names = [c.first_name for c in (*first.items, *rest.items)]
    assert sorted(n or "" for n in names) == [f"Lee{n}" for n in range(5)]
    assert rest.next_cursor is None


async def test_search_never_crosses_tenants(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    alice, bob = (_principal(user) for user in two_users)
    contacts, targets = _contacts(sessions), _targets(sessions)
    await _add(contacts, alice, first_name="Shared", company="Tailspin")
    await _add(contacts, bob, first_name="Shared", company="Tailspin")
    await _add(contacts, bob, first_name="BobOnly", company="Tailspin")
    assert await _found(contacts, alice, "tailspin") == ["Shared"]
    assert await _found(contacts, bob, "tailspin") == ["BobOnly", "Shared"]

    await targets.create_target(alice, {"name": "Tailspin Toys", "lane": "A"})
    await targets.create_target(bob, {"name": "Tailspin Air", "lane": "A"})
    mine = await targets.list_targets(alice, None, None, None, 50, q="tailspin")
    assert [t.name for t in mine.items] == ["Tailspin Toys"]


async def test_target_search_covers_its_text_columns(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    alice = _principal(two_users[0])
    targets = _targets(sessions)
    await targets.create_target(alice, {"name": "Northwind", "lane": "A", "why": "Big on dbt"})
    await targets.create_target(alice, {"name": "Contoso", "lane": "A", "notes": "Met at a MEETUP"})
    await targets.create_target(alice, {"name": "Fabrikam", "lane": "B", "approach": "Via dbt"})

    async def names(q: str, lane: str | None = None) -> list[str]:
        page = await targets.list_targets(alice, None, lane, None, 50, q=q)
        return sorted(t.name for t in page.items)

    assert await names("NORTH") == ["Northwind"]
    assert await names("meetup") == ["Contoso"]
    assert await names("dbt") == ["Fabrikam", "Northwind"]
    assert await names("dbt", lane="B") == ["Fabrikam"]
