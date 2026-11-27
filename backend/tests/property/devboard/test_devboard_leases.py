"""Lease overlap is exact (segment prefixes) and no two runs ever hold overlapping leases."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from app.adapters.fakes.devboard import InMemoryLeaseStore
from app.adapters.fakes.system import FakeClock, SequentialIds
from app.domain.devboard.leases import LeaseService
from app.domain.devboard.model import Actor, ActorKind
from app.domain.devboard.paths import overlaps

segment = st.sampled_from(["features", "board", "lib", "api", "x.ts", "y.ts", "a", "b"])


def _join(root: str, parts: list[str], is_dir: bool) -> str:
    return "/".join([root, *parts]) + ("/" if is_dir else "")


paths = st.builds(
    _join,
    st.sampled_from(["web/src", "web/tests", "backend/src/app/api", "backend/src/app/domain"]),
    st.lists(segment, max_size=3),
    st.booleans(),
)


@given(paths, paths)
def test_overlap_is_symmetric_and_matches_segment_prefixes(first: str, second: str) -> None:
    assert overlaps(first, second) == overlaps(second, first)
    left, right = first.rstrip("/").split("/"), second.rstrip("/").split("/")
    shorter = min(len(left), len(right))
    assert overlaps(first, second) == (left[:shorter] == right[:shorter])


@given(paths)
def test_a_path_overlaps_itself_and_its_parents(path: str) -> None:
    assert overlaps(path, path)
    parent = path.rstrip("/").rsplit("/", 1)[0] + "/"
    assert overlaps(parent, path)


ACTOR = Actor(uuid.UUID(int=1), ActorKind.AGENT)
claims = st.lists(
    st.tuples(
        st.integers(min_value=0, max_value=3),  # which run
        st.lists(paths, min_size=1, max_size=3),
        st.integers(min_value=0, max_value=20),  # minutes to advance first
        st.sampled_from(["claim", "renew", "release", "reclaim"]),
    ),
    max_size=25,
)


@settings(max_examples=150, deadline=None)
@given(claims)
def test_active_leases_of_different_runs_never_overlap(
    steps: list[tuple[int, list[str], int, str]],
) -> None:
    asyncio.run(_exercise(steps))


async def _exercise(steps: list[tuple[int, list[str], int, str]]) -> None:
    clock = FakeClock(datetime(2026, 11, 25, tzinfo=UTC))
    store = InMemoryLeaseStore(SequentialIds())
    service = LeaseService(store, clock)
    runs = [uuid.UUID(int=100 + index) for index in range(4)]
    for run_index, requested, minutes, action in steps:
        clock.advance(timedelta(minutes=minutes))
        run = runs[run_index]
        if action == "claim":
            outcome = await service.claim(ACTOR, run, uuid.UUID(int=run_index), tuple(requested))
            assert bool(outcome.granted) != bool(outcome.conflicts)
        elif action == "renew":
            await service.heartbeat(ACTOR, run)
        elif action == "release":
            await service.release_run(ACTOR, run)
        else:
            await service.reclaim_expired(ACTOR)
        active = await service.active(ACTOR)
        for index, lease in enumerate(active):
            for other in active[index + 1 :]:
                if lease.run_id != other.run_id:
                    assert not overlaps(lease.path, other.path)
