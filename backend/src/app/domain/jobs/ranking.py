"""Fractional ranks for the pipeline board (spec: `rank` is compared in byte order).

Keys are base-62 strings read as fractions in [0, 1): `key_between(a, b)` returns a key strictly
between its neighbours. Generated keys never end in "0", so there is always room between two of
them. The alphabet is in ASCII order, so Python string comparison matches `COLLATE "C"`.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Sequence

DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
MAX_RANK_CHARS = 64
_VALID = re.compile(r"^[0-9A-Za-z]{1,64}$")


def is_valid_rank(rank: str) -> bool:
    return bool(_VALID.match(rank))


def _midpoint(low: str, high: str | None) -> str:
    """A key strictly between `low` and `high` (`""` is 0, `None` is 1). No trailing zeros."""
    if high is not None:
        shared = 0
        while shared < len(high) and (low[shared] if shared < len(low) else "0") == high[shared]:
            shared += 1
        if shared > 0:
            return high[:shared] + _midpoint(low[shared:], high[shared:])
    low_digit = DIGITS.index(low[0]) if low else 0
    high_digit = DIGITS.index(high[0]) if high else len(DIGITS)
    if high_digit - low_digit > 1:
        return DIGITS[(low_digit + high_digit + 1) // 2]
    if high is not None and len(high) > 1:
        return high[:1]
    return DIGITS[low_digit] + _midpoint(low[1:], None)


def key_between(before: str | None, after: str | None) -> str | None:
    """A rank that sorts after `before` and before `after` (either may be absent).

    Returns `None` when no such key exists, which only happens when `after` is all zeros or the
    neighbours are adjacent client-chosen keys; callers then rebalance the column.
    """
    low = (before or "").rstrip("0")
    high = after.rstrip("0") if after is not None else None
    if high is not None and (high == "" or low >= high):
        return None
    key = _midpoint(low, high)
    if before is not None and key <= before:
        return None
    if after is not None and key >= after:
        return None
    return key


def spread(count: int) -> list[str]:
    """`count` increasing keys, evenly spaced, for a rebalanced column."""
    width = 1
    while len(DIGITS) ** width <= count:
        width += 1
    step = len(DIGITS) ** width // (count + 1)
    keys: list[str] = []
    for position in range(1, count + 1):
        keys.append(_encode(position * step, width).rstrip("0") or DIGITS[1])
    return keys


def _encode(value: int, width: int) -> str:
    chars: list[str] = []
    for _ in range(width):
        value, digit = divmod(value, len(DIGITS))
        chars.append(DIGITS[digit])
    return "".join(reversed(chars))


def is_strictly_ordered(ranks: Sequence[str]) -> bool:
    return all(left < right for left, right in itertools.pairwise(ranks))
