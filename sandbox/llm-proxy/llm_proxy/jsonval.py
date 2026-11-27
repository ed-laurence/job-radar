"""Typed views over parsed JSON (``json.loads`` returns ``Any``)."""

from __future__ import annotations

from typing import cast


def as_object(value: object) -> dict[str, object] | None:
    return cast("dict[str, object]", value) if isinstance(value, dict) else None


def as_int(value: object) -> int | None:
    """An int that is not a bool."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def as_list(value: object) -> list[object]:
    return cast("list[object]", value) if isinstance(value, list) else []
