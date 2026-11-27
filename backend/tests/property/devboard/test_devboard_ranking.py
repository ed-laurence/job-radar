"""Fractional ranks: keys land strictly between their neighbours, stay valid, and a column's
ordering stays total."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from app.adapters.fakes.devboard import InMemoryTaskStore
from app.domain.devboard.model import Column, TaskRecord
from app.domain.devboard.placement import place_at
from app.domain.devboard.ranking import DIGITS, is_valid_rank, key_between, spread

ranks = st.text(alphabet=DIGITS, min_size=1, max_size=12)


@given(ranks, ranks)
def test_key_between_is_strictly_between(first: str, second: str) -> None:
    if first == second:
        return
    lower, upper = sorted((first, second))
    key = key_between(lower, upper)
    if key is None:
        # The only gap-free pair: the upper key is the lower key plus trailing zeros.
        assert upper.startswith(lower)
        assert set(upper[len(lower) :]) == {"0"}
        return
    assert lower < key < upper
    assert is_valid_rank(key)


@given(ranks)
def test_keys_after_and_before_exist(rank: str) -> None:
    after = key_between(rank, None)
    assert after is not None
    assert after > rank
    assert is_valid_rank(after)
    before = key_between(None, rank)
    if before is not None:
        assert before < rank
        assert is_valid_rank(before)


@given(st.integers(min_value=1, max_value=500))
def test_spread_is_strictly_increasing_and_valid(count: int) -> None:
    keys = spread(count)
    assert len(keys) == count
    assert keys == sorted(set(keys))
    assert all(is_valid_rank(key) for key in keys)


@settings(max_examples=100, deadline=None)
@given(st.lists(st.text(alphabet="0Uz", min_size=1, max_size=3), min_size=1, max_size=40))
def test_placement_keeps_ranks_unique_even_when_clients_collide(requested: list[str]) -> None:
    asyncio.run(_place_all(requested))


async def _place_all(requested: list[str]) -> None:
    store = InMemoryTaskStore()
    user = uuid.UUID(int=1)
    now = datetime(2026, 11, 25, tzinfo=UTC)
    for index, rank in enumerate(requested):
        task_id = uuid.UUID(int=10 + index)
        placed = await place_at(store, user, Column.BACKLOG, task_id, rank)
        await store.insert(
            user,
            TaskRecord(task_id, "t", "", Column.BACKLOG, placed, 1, user, now, now),
        )
        ranks = [rank for rank, _ in await store.column_ranks(user, Column.BACKLOG)]
        assert len(set(ranks)) == len(ranks)
        assert all(is_valid_rank(rank) for rank in ranks)
