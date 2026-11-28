"""The rubric ids a BLOCKER may cite come from docs/standards/review-rubric.yaml."""

from __future__ import annotations

import pytest

from app.adapters.standards import (
    RUBRIC_FILE,
    RubricFileError,
    StandardsRubricIds,
    parse_rubric_ids,
)
from app.settings import REPO_ROOT


def test_the_repo_rubric_is_read() -> None:
    ids = StandardsRubricIds(REPO_ROOT / RUBRIC_FILE).ids()
    assert {"ARCH-03", "SEC-06", "TEST-01"} <= ids
    assert all("-" in rubric_id for rubric_id in ids)


def test_items_without_an_id_are_skipped() -> None:
    text = "version: 1\nitems:\n  - {id: ARCH-01, text: x}\n  - {text: no id}\n  - plain\n"
    assert parse_rubric_ids(text) == {"ARCH-01"}


@pytest.mark.parametrize("text", ["", "- a list", "items: nope", "items: []"])
def test_a_rubric_without_ids_is_refused(text: str) -> None:
    with pytest.raises(RubricFileError):
        parse_rubric_ids(text)
