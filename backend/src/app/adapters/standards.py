"""`RubricIds` from `docs/standards/review-rubric.yaml` (`items: [{id: ARCH-01, ...}, ...]`).

The file is read once, when the adapter is built at startup, so `ids()` never does I/O.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import yaml

RUBRIC_FILE = Path("docs") / "standards" / "review-rubric.yaml"


class RubricFileError(ValueError):
    """The rubric file has no `items` list of objects with an `id`."""


class StandardsRubricIds:
    def __init__(self, path: Path) -> None:
        self._ids = parse_rubric_ids(path.read_text("utf-8"))

    def ids(self) -> frozenset[str]:
        return self._ids


def parse_rubric_ids(text: str) -> frozenset[str]:
    loaded: object = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise RubricFileError("The review rubric is not a mapping.")
    items = cast("dict[str, object]", loaded).get("items")
    if not isinstance(items, list):
        raise RubricFileError("The review rubric has no `items` list.")
    ids: set[str] = set()
    for item in cast("list[object]", items):
        if isinstance(item, dict):
            value = cast("dict[str, object]", item).get("id")
            if isinstance(value, str) and value.strip():
                ids.add(value.strip())
    if not ids:
        raise RubricFileError("The review rubric lists no ids.")
    return frozenset(ids)
