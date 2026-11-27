"""Pipeline ordering stays total and stable under random drag-and-drop moves."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.jobs.ranking import (
    MAX_RANK_CHARS,
    is_strictly_ordered,
    is_valid_rank,
    key_between,
    spread,
)

# Each move: take the item at `source % len` and drop it at `target % len` (after removal).
moves = st.lists(st.tuples(st.integers(0, 10_000), st.integers(0, 10_000)), max_size=120)


def _place(ranks: list[str], index: int) -> str:
    before = ranks[index - 1] if index > 0 else None
    after = ranks[index] if index < len(ranks) else None
    key = key_between(before, after)
    assert key is not None
    return key


@settings(max_examples=200, deadline=None)
@given(start=st.integers(1, 30), steps=moves)
def test_random_moves_keep_a_total_order(start: int, steps: list[tuple[int, int]]) -> None:
    items = list(range(start))
    ranks = spread(start)
    for source, target in steps:
        position = source % len(items)
        item = items.pop(position)
        ranks.pop(position)
        destination = target % (len(items) + 1)
        rank = _place(ranks, destination)
        items.insert(destination, item)
        ranks.insert(destination, rank)
        assert is_strictly_ordered(ranks)
        assert is_valid_rank(rank)
    # Sorting by rank alone reproduces the board order: the ordering is total.
    by_rank = [item for _, item in sorted(zip(ranks, items, strict=True))]
    assert by_rank == items


@settings(max_examples=200, deadline=None)
@given(count=st.integers(1, 5_000))
def test_spread_is_strictly_increasing_and_valid(count: int) -> None:
    keys = spread(count)
    assert len(keys) == count
    assert is_strictly_ordered(keys)
    assert all(is_valid_rank(key) and not key.endswith("0") for key in keys)


@given(st.lists(st.text(alphabet="123456789abcXYZ", min_size=1, max_size=8), min_size=2))
def test_key_between_any_two_distinct_keys(raw: list[str]) -> None:
    low, high = sorted(set(raw))[:2] if len(set(raw)) > 1 else (raw[0], None)
    key = key_between(low, high)
    assert key is not None
    assert low < key
    if high is not None:
        assert key < high


def test_top_inserts_stay_within_the_length_limit_for_hundreds_of_moves() -> None:
    first = "a"
    for _ in range(300):
        key = key_between(None, first)
        assert key is not None
        assert key < first
        first = key
    assert len(first) <= MAX_RANK_CHARS


def test_no_key_before_all_zeros() -> None:
    assert key_between(None, "000") is None
    assert key_between("b", "b") is None
