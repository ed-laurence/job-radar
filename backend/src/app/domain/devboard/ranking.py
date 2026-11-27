"""Fractional ranks: base-62 keys compared in byte order (`COLLATE "C"`).

The ASCII order of `0-9A-Za-z` is the digit order, so byte order and numeric order agree. A
key exists strictly between two keys unless the upper one is the lower one followed only by
`0`s; callers then rebalance the column with `spread`.
"""

from __future__ import annotations

import re

DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
BASE = len(DIGITS)
MAX_RANK_CHARS = 64
_VALID = re.compile(r"^[0-9A-Za-z]{1,64}$")


def is_valid_rank(rank: str) -> bool:
    return bool(_VALID.match(rank))


def _digit(key: str, index: int) -> int:
    """The digit at `index`, or -1 past the end (a shorter key sorts first)."""
    return DIGITS.index(key[index]) if index < len(key) else -1


def key_between(lower: str | None, upper: str | None) -> str | None:
    """The shortest-ish key with `lower < key < upper` (None means unbounded), or None when no
    such key exists or it would exceed `MAX_RANK_CHARS`."""
    low = lower or ""
    if upper is not None and low >= upper:
        raise ValueError("lower must sort before upper")
    out: list[str] = []
    index = 0
    while True:
        low_digit = _digit(low, index)
        high_digit = BASE if upper is None else _digit(upper, index)
        if high_digit - low_digit >= 2:
            out.append(DIGITS[(low_digit + high_digit) // 2])
            return _bounded("".join(out))
        if high_digit - low_digit == 1:
            if low_digit >= 0:
                out.append(DIGITS[low_digit])
                return _bounded("".join(out) + _above(low, index + 1))
            # `low` has ended and the upper key has a `0` here: only a longer upper key leaves
            # room, and then this prefix itself is the answer.
            assert upper is not None
            out.append(DIGITS[0])
            return _bounded("".join(out)) if len(upper) > index + 1 else None
        out.append(DIGITS[low_digit])
        index += 1


def _above(low: str, index: int) -> str:
    """A suffix that makes `low[:index] + suffix` sort after `low` (no upper bound)."""
    suffix: list[str] = []
    while True:
        digit = _digit(low, index)
        if digit < BASE - 1:
            suffix.append(DIGITS[(digit + BASE) // 2])
            return "".join(suffix)
        suffix.append(DIGITS[digit])
        index += 1


def _bounded(key: str) -> str | None:
    return key if len(key) <= MAX_RANK_CHARS else None


def key_after(last: str | None) -> str | None:
    return key_between(last, None)


def spread(count: int) -> list[str]:
    """`count` evenly spaced, strictly increasing keys of equal width (for a rebalance)."""
    width = 1
    while BASE**width <= count + 1:
        width += 1
    step = BASE**width // (count + 1)
    return [_encode((position + 1) * step, width) for position in range(count)]


def _encode(value: int, width: int) -> str:
    chars: list[str] = []
    for _ in range(width):
        value, digit = divmod(value, BASE)
        chars.append(DIGITS[digit])
    return "".join(reversed(chars))
