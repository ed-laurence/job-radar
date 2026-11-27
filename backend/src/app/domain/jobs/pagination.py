"""Opaque cursors: a versioned, base64url-encoded offset into a stable sort (ties break on id)."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Sequence
from typing import cast

from app.domain.errors import BadRequest
from app.domain.jobs.records import Page

MAX_OFFSET = 1_000_000
_INVALID = "That page link is no longer valid. Start from the first page."


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload: object = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeError, binascii.Error):
        raise BadRequest(_INVALID) from None
    if not isinstance(payload, dict):
        raise BadRequest(_INVALID)
    offset = cast("dict[str, object]", payload).get("o")
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= MAX_OFFSET:
        raise BadRequest(_INVALID)
    return offset


def encode_cursor(offset: int) -> str:
    raw = json.dumps({"o": offset}, separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def to_page[T](fetched: Sequence[T], offset: int, limit: int) -> Page[T]:
    """`fetched` holds up to `limit + 1` rows; the extra one only signals another page."""
    has_more = len(fetched) > limit
    items = tuple(fetched[:limit])
    return Page(items=items, next_cursor=encode_cursor(offset + limit) if has_more else None)
