"""Path rules (write scope, risk map) and lease behaviour (hotspots, TTL, heartbeat, reclaim)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.adapters.fakes.devboard import InMemoryLeaseStore
from app.adapters.fakes.system import FakeClock, SequentialIds
from app.domain.devboard.leases import LeaseService
from app.domain.devboard.model import Actor, ActorKind, LeaseKind, Risk
from app.domain.devboard.paths import InvalidPath, is_writable, normalise, path_risk
from app.domain.errors import ValidationFailed

ACTOR = Actor(uuid.UUID(int=1), ActorKind.AGENT)
RUN_A, RUN_B = uuid.UUID(int=10), uuid.UUID(int=11)
TASK_A, TASK_B = uuid.UUID(int=20), uuid.UUID(int=21)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("web/src/features/board/Card.tsx", True),
        ("backend/src/app/domain/", True),
        ("api/openapi.yaml", True),
        ("docs/features/dev-board.md", True),
        ("infra/envs/prod/main.tf", False),
        (".github/workflows/ci.yml", False),
        ("backend/src/app/adapters/db/x.py", False),
        ("backend/src/app/api/security.py", False),
        ("backend/tests/unit/conftest.py", False),
        ("web/vitest.config.ts", False),
        ("backend/migrations/versions/0002.py", False),
        ("docs/standards/coding.md", False),
    ],
)
def test_write_scope_is_an_allowlist(path: str, expected: bool) -> None:
    assert is_writable(path) is expected


@pytest.mark.parametrize("raw", ["web/src/*", "/etc/passwd", "web/../infra/", "web//src", ""])
def test_paths_are_concrete(raw: str) -> None:
    with pytest.raises(InvalidPath):
        normalise(raw)


def test_risk_map_most_specific_wins_and_hotspots_are_high() -> None:
    assert path_risk("web/src/features/board/") is Risk.LOW
    assert path_risk("backend/src/app/domain/x.py") is Risk.MEDIUM
    assert path_risk("web/src/lib/api/client.ts") is Risk.HIGH
    assert path_risk("web/src/") is Risk.HIGH  # contains the web/src/lib hotspot


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def leases(clock: FakeClock) -> LeaseService:
    return LeaseService(InMemoryLeaseStore(SequentialIds()), clock)


async def test_overlapping_claims_are_refused(leases: LeaseService) -> None:
    first = await leases.claim(ACTOR, RUN_A, TASK_A, ("web/src/features/board/",))
    assert [lease.path for lease in first.granted] == ["web/src/features/board/"]
    clash = await leases.claim(ACTOR, RUN_B, TASK_B, ("web/src/features/board/Card.tsx",))
    assert clash.granted == ()
    assert [lease.run_id for lease in clash.conflicts] == [RUN_A]
    sibling = await leases.claim(ACTOR, RUN_B, TASK_B, ("web/src/features/contacts/",))
    assert sibling.conflicts == ()


async def test_claims_are_all_or_nothing(leases: LeaseService) -> None:
    await leases.claim(ACTOR, RUN_A, TASK_A, ("web/src/features/board/",))
    refused = await leases.claim(
        ACTOR, RUN_B, TASK_B, ("web/src/features/contacts/", "web/src/features/board/x.ts")
    )
    assert refused.granted == ()
    assert [lease.run_id for lease in await leases.active(ACTOR)] == [RUN_A]


async def test_hotspots_take_a_single_writer_lock(leases: LeaseService) -> None:
    first = await leases.claim(ACTOR, RUN_A, TASK_A, ("web/src/lib/format.ts",))
    assert [(lease.path, lease.kind) for lease in first.granted] == [
        ("web/src/lib/", LeaseKind.HOTSPOT)
    ]
    other_file = await leases.claim(ACTOR, RUN_B, TASK_B, ("web/src/lib/tokens.css",))
    assert other_file.conflicts


async def test_leases_expire_unless_renewed_and_the_watchdog_reclaims(
    leases: LeaseService, clock: FakeClock
) -> None:
    await leases.claim(ACTOR, RUN_A, TASK_A, ("backend/src/app/api/",), ttl=timedelta(minutes=5))
    clock.advance(timedelta(minutes=4))
    assert await leases.heartbeat(ACTOR, RUN_A, ttl=timedelta(minutes=5)) == 1
    clock.advance(timedelta(minutes=4))
    assert len(await leases.active(ACTOR)) == 1
    clock.advance(timedelta(minutes=2))
    assert await leases.active(ACTOR) == []
    assert await leases.heartbeat(ACTOR, RUN_A) == 0  # an expired lease must be re-claimed
    taken = await leases.claim(ACTOR, RUN_B, TASK_B, ("backend/src/app/api/x.py",))
    assert taken.granted
    assert await leases.reclaim_expired(ACTOR) == 1


async def test_bad_claims_are_validation_errors(leases: LeaseService) -> None:
    with pytest.raises(ValidationFailed) as error:
        await leases.claim(ACTOR, RUN_A, TASK_A, ("infra/main.tf", "web/src/**"))
    assert [item.pointer for item in error.value.errors] == ["/paths/0", "/paths/1"]
    with pytest.raises(ValidationFailed):
        await leases.claim(ACTOR, RUN_A, TASK_A, ("web/src/x.ts",), ttl=timedelta(days=1))


async def test_release_by_task_frees_every_run(leases: LeaseService) -> None:
    await leases.claim(ACTOR, RUN_A, TASK_A, ("web/src/features/a/",))
    await leases.claim(ACTOR, RUN_B, TASK_A, ("web/src/features/b/",))
    assert await leases.release_task(ACTOR, TASK_A) == 2
    assert await leases.active(ACTOR) == []
