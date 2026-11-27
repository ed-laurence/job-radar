"""A card's body rebuilt from a description and criteria reads back as the same two fields, so
an accepted proposal changes exactly what it showed and nothing else."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from app.domain.devboard.proposals import compose_body, split_body

_WORDS = st.text(alphabet=st.characters(whitelist_categories=("L", "N")), min_size=1, max_size=8)
_LINE = st.lists(_WORDS, min_size=1, max_size=6).map(" ".join)
descriptions = st.lists(_LINE, max_size=4).map("\n".join)
criteria = st.lists(_LINE, max_size=6).map(tuple)


@given(descriptions, criteria)
def test_compose_then_split_returns_the_same_fields(
    description: str, items: tuple[str, ...]
) -> None:
    assert split_body(compose_body(description, items)) == (description.strip(), items)


@given(descriptions, criteria, criteria)
def test_rewriting_the_criteria_keeps_the_description(
    description: str, first: tuple[str, ...], second: tuple[str, ...]
) -> None:
    body = compose_body(description, first)
    kept, _ = split_body(body)
    assert split_body(compose_body(kept, second)) == (description.strip(), second)
