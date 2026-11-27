"""The job-search Postgres adapters under row-level security, and the seed loader."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, cast

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.jobs_roles import PgRoles
from app.adapters.db.jobs_stores import PgChats, PgProfiles, PgResearch, PgRows
from app.adapters.db.tables_jobs import Contact, Target
from app.adapters.fakes.security import FakeContentGuard
from app.adapters.fakes.system import FakeClock, SequentialIds
from app.domain.errors import Conflict, NotFound, ValidationFailed
from app.domain.identity import AppRole, Principal
from app.domain.jobs.crm import ContactService, TargetService
from app.domain.jobs.ports import RoleQuery
from app.domain.jobs.records import ChatRecord, ContactRecord, StepRecord, TargetRecord
from app.domain.jobs.roles import RejectionInput, RoleDraft, RoleService, StatusChange
from tests.integration.jobs.conftest import SEED_OWNER, Seeded

pytestmark = pytest.mark.integration


async def _rows(owner: asyncpg.Connection, sql: str, *args: object) -> list[dict[str, Any]]:
    """asyncpg records are untyped; ground-truth values are compared loosely by name."""
    records = cast("list[Mapping[str, Any]]", await owner.fetch(sql, *args))
    return [dict(record) for record in records]


async def _row(owner: asyncpg.Connection, sql: str, *args: object) -> dict[str, Any] | None:
    rows = await _rows(owner, sql, *args)
    return rows[0] if rows else None


type Sessions = async_sessionmaker[AsyncSession]


def _principal(user_id: uuid.UUID) -> Principal:
    return Principal(user_id=user_id, email="x@example.test", role=AppRole.USER)


@pytest.fixture
def roles(sessions: Sessions) -> RoleService:
    return RoleService(
        PgRoles(sessions),
        FakeContentGuard(),
        FakeClock(),
        SequentialIds(start=uuid.uuid4().int % 10**9),
    )


async def test_role_lifecycle_under_rls(
    sessions: Sessions, roles: RoleService, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    alice, bob = (_principal(user) for user in two_users)
    role = await roles.create_role(alice, RoleDraft(title="Data Engineer", company="Acme Pty Ltd"))
    with pytest.raises(NotFound):
        await roles.get_role(bob, role.id)
    assert (await roles.list_roles(bob, RoleQuery(), None, 10)).items == ()
    updated = await roles.update_role(alice, role.id, 1, {"notes": "Call Sam."})
    assert updated.version == 2
    with pytest.raises(Conflict) as stale:
        await roles.update_role(alice, role.id, 1, {"notes": "again"})
    assert stale.value.current_version == 2
    with pytest.raises(NotFound):
        await roles.update_role(bob, role.id, 2, {"notes": "not mine"})
    change = StatusChange(
        status="rejected", version=2, rejection=RejectionInput(reason_categories=("pay",))
    )
    rejected = await roles.change_status(alice, role.id, change)
    assert rejected.status == "rejected"
    assert rejected.version == 3
    store = PgRoles(sessions)
    rejection = await store.get_rejection(alice.user_id, role.id)
    assert rejection is not None
    assert rejection.reason_categories == ("pay",)
    kinds = [e.kind for e in (await roles.list_events(alice, role.id, None, 10)).items]
    assert kinds == ["status", "note", "status"]
    await roles.delete_role(alice, role.id, 3)
    with pytest.raises(NotFound):
        await roles.get_role(alice, role.id)


async def test_rank_order_and_filters(
    roles: RoleService, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    alice = _principal(two_users[0])
    created = [await roles.create_role(alice, RoleDraft(title=f"Role {n}")) for n in range(3)]
    page = await roles.list_roles(alice, RoleQuery(statuses=("new",)), None, 10)
    assert [r.id for r in page.items] == [r.id for r in reversed(created)]
    await roles.reorder(alice, created[2].id, "zz", 1)
    page = await roles.list_roles(alice, RoleQuery(statuses=("new",), text="ROLE"), None, 10)
    assert page.items[-1].id == created[2].id


async def test_contacts_and_targets_keep_natural_keys_unique(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    alice, bob = (_principal(user) for user in two_users)
    clock, ids = FakeClock(), SequentialIds(start=uuid.uuid4().int % 10**9)
    contacts = ContactService(PgRows(sessions, Contact, ContactRecord, "url"), clock, ids)
    first = await contacts.create_contact(alice, {"first_name": "Jo", "company": "Acme Pty Ltd"})
    assert first.url is None
    second = await contacts.create_contact(alice, {"first_name": "Sam"})
    assert second.url is None
    url = {"first_name": "Lee", "url": "https://people.example.test/in/lee"}
    await contacts.create_contact(alice, url)
    with pytest.raises(ValidationFailed):
        await contacts.create_contact(alice, url)
    await contacts.create_contact(bob, url)  # unique per user, not globally
    found = await contacts.list_contacts(alice, None, "ACME", None, 10)
    assert [c.first_name for c in found.items] == ["Jo"]
    targets = TargetService(PgRows(sessions, Target, TargetRecord, "name"), clock, ids)
    target = await targets.create_target(alice, {"name": "Acme Pty Ltd", "lane": "A"})
    with pytest.raises(ValidationFailed):
        await targets.create_target(alice, {"name": "ACME", "lane": "B"})
    approached = await targets.update_target(alice, target.id, 1, {"status": "approached"})
    assert approached.approached_at is not None
    with pytest.raises(NotFound):
        await targets.get_target(bob, target.id)


async def test_chats_and_research_steps(
    sessions: Sessions, roles: RoleService, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    alice = _principal(two_users[0])
    role = await roles.create_role(alice, RoleDraft(title="Data Engineer"))
    chats, clock = PgChats(sessions), FakeClock()
    for author in ("user", "assistant", "user"):
        clock.advance(timedelta(seconds=1))
        await chats.add_message(
            alice.user_id,
            ChatRecord(
                id=uuid.uuid4(),
                role_id=role.id,
                author=author,
                content=author,
                created_at=clock.now(),
            ),
        )
    assert [m.author for m in await chats.recent_messages(alice.user_id, role.id, 2)] == [
        "assistant",
        "user",
    ]
    await chats.clear(alice.user_id, role.id)
    assert await chats.list_messages(alice.user_id, role.id, 0, 10) == []
    targets = TargetService(
        PgRows(sessions, Target, TargetRecord, "name"),
        clock,
        SequentialIds(start=uuid.uuid4().int % 10**9),
    )
    target = await targets.create_target(alice, {"name": "Research Co", "lane": "C"})
    research = PgResearch(sessions)
    run = await research.create_run(
        alice.user_id,
        {
            "id": uuid.uuid4(),
            "target_id": target.id,
            "status": "queued",
            "focus": ["news"],
            "created_at": clock.now(),
        },
    )
    for seq in (1, 2, 3):
        await research.add_step(
            alice.user_id,
            StepRecord(
                id=uuid.uuid4(),
                run_id=run.id,
                seq=seq,
                kind="thought",
                summary=f"s{seq}",
                at=clock.now(),
            ),
        )
    assert [s.seq for s in await research.steps_after(alice.user_id, run.id, 1)] == [2, 3]
    done = await research.update_run(
        alice.user_id, run.id, {"status": "succeeded", "sources": ["https://a.example.test/"]}
    )
    assert done.status == "succeeded"
    assert done.sources == ("https://a.example.test/",)
    assert await research.get_run(two_users[1], run.id) is None


async def test_seed_loads_idempotently_with_rebased_dates(
    seeded: Seeded, owner: asyncpg.Connection
) -> None:
    # The seed has no dev-board data: only the account's job-search rows load.
    assert list(seeded.first) == [SEED_OWNER]
    owner_rows = seeded.first[SEED_OWNER]
    assert owner_rows > 10_000
    assert all(value == 0 for value in seeded.second.values())
    counts = await _row(
        owner,
        "SELECT (SELECT count(*) FROM roles WHERE user_id = $1) AS roles,"
        " (SELECT count(*) FROM role_pool WHERE user_id = $1) AS pooled,"
        " (SELECT count(*) FROM role_pool WHERE user_id = $1 AND released_at IS NULL) AS waiting,"
        " (SELECT count(*) FROM llm_calls WHERE user_id = $1) AS calls,"
        " (SELECT count(*) FROM dev_tasks WHERE created_by = $1) AS tasks,"
        " (SELECT max(found_at) FROM roles WHERE user_id = $1) AS newest",
        seeded.user_id,
    )
    assert counts is not None
    # 300 roles start visible; the other 1219 seed roles wait in the pool for scans.
    assert counts["roles"] == 300
    assert counts["pooled"] == counts["waiting"] == 1519 - 300
    assert counts["calls"] == 8035
    assert counts["tasks"] == 0
    # The newest role sits at the anchor plus the clock's offset from it (one hour).
    assert counts["newest"].isoformat().startswith("2026-11-2")
    user = await _row(owner, "SELECT email, role FROM users WHERE id = $1", seeded.user_id)
    assert user is not None
    assert (user["email"], user["role"]) == ("seed-owner@example.test", "maintainer")


async def test_profile_comes_from_the_users_own_records(seeded: Seeded, sessions: Sessions) -> None:
    profile = await PgProfiles(sessions).load_profile(seeded.user_id)
    assert profile.name == "Alex Example"
    assert profile.location == "Melbourne VIC"
    assert len(profile.experience) == 13
    assert len(profile.lessons) == 1
    assert (await PgProfiles(sessions).load_profile(uuid.uuid4())).name is None


async def test_list_rejections_skips_deleted_roles_and_other_users(
    sessions: Sessions, roles: RoleService, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    alice = _principal(two_users[0])
    kept = await roles.create_role(alice, RoleDraft(title="Kept"))
    gone = await roles.create_role(alice, RoleDraft(title="Gone"))
    for role in (kept, gone):
        await roles.change_status(alice, role.id, StatusChange(status="rejected", version=1))
    await roles.delete_role(alice, gone.id, 2)
    store = PgRoles(sessions)
    assert [r.role_id for r in await store.list_rejections(alice.user_id, 10)] == [kept.id]
    assert await store.list_rejections(two_users[1], 10) == []
