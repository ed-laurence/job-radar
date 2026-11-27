"""Per-model prices (USD per million tokens) and request costs in micro-USD (stdlib only).

The table is the ``pricing:`` section of the repo's ``ai/models.yaml`` (``prices:`` is accepted
as an alias), mounted read-only into the container (``LLM_PROXY_PRICES``). Only the flow-mapping
form used there is understood::

    pricing:
      claude-opus-5-5: {input: 4.00, output: 20.00, cache_read: 0.40}

Anything else in the section is an error: the proxy refuses to start rather than guess.
``cache_read`` and ``cache_write`` come from the table when present; otherwise they default to
Anthropic's documented multipliers relative to input (read 0.1x, 5-minute write 1.25x). Those
multipliers are an assumption to verify against the provider's current price list.

Cost arithmetic: tokens x (USD per Mtok) = micro-USD, exactly, so costs are Decimals rounded up
to whole micro-USD.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

CACHE_READ_MULTIPLIER = Decimal("0.1")
CACHE_WRITE_MULTIPLIER = Decimal("1.25")
BYTES_PER_TOKEN = 4
SECTION_NAMES = ("pricing", "prices")
_KNOWN_KEYS = frozenset({"input", "output", "cache_read", "cache_write"})
_ENTRY = re.compile(r"^\s+(?P<model>[A-Za-z0-9][A-Za-z0-9._/:-]*)\s*:\s*\{(?P<body>[^{}]*)\}\s*$")
_TOP_KEY = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*:")


class PriceTableError(ValueError):
    pass


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write: Decimal


@dataclass(frozen=True)
class TokenCounts:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


def _decimal(model: str, key: str, raw: str) -> Decimal:
    try:
        value = Decimal(raw.strip())
    except InvalidOperation:
        raise PriceTableError(f"{model}.{key}: not a number") from None
    if not value.is_finite() or value < 0:
        raise PriceTableError(f"{model}.{key}: must be a non-negative number")
    return value


def _entry(model: str, body: str) -> Price:
    fields: dict[str, Decimal] = {}
    for part in body.split(","):
        if not part.strip():
            continue
        key, sep, raw = part.partition(":")
        key = key.strip()
        if not sep or key not in _KNOWN_KEYS:
            raise PriceTableError(f"{model}: unknown or malformed field {key!r}")
        fields[key] = _decimal(model, key, raw)
    if "input" not in fields or "output" not in fields:
        raise PriceTableError(f"{model}: input and output prices are required")
    return Price(
        input=fields["input"],
        output=fields["output"],
        cache_read=fields.get("cache_read", fields["input"] * CACHE_READ_MULTIPLIER),
        cache_write=fields.get("cache_write", fields["input"] * CACHE_WRITE_MULTIPLIER),
    )


def parse_price_table(text: str) -> dict[str, Price]:
    """The models.yaml price section. Raises PriceTableError when it is missing or malformed."""
    prices: dict[str, Price] = {}
    in_section = found = False
    for raw_line in text.splitlines():
        line = raw_line.split(" #", 1)[0].rstrip() if not raw_line.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        top = _TOP_KEY.match(line)
        if top:
            in_section = top.group("key") in SECTION_NAMES
            found = found or in_section
            if in_section and line[top.end() :].strip():
                raise PriceTableError("the price section must be a block mapping")
            continue
        if not in_section:
            continue
        entry = _ENTRY.match(line)
        if entry is None:
            raise PriceTableError("price entries must look like 'model: {input: N, output: N}'")
        model = entry.group("model")
        if model in prices:
            raise PriceTableError(f"{model}: listed twice")
        prices[model] = _entry(model, entry.group("body"))
    if not found:
        raise PriceTableError(f"no {' or '.join(SECTION_NAMES)} section")
    if not prices:
        raise PriceTableError("the price section is empty")
    return prices


def load_price_table(path: Path) -> dict[str, Price]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PriceTableError(f"cannot read price table {path}: {type(exc).__name__}") from None
    return parse_price_table(text)


def _micro(amount: Decimal) -> int:
    return max(0, math.ceil(amount))


def cost_usd_micro(price: Price, usage: TokenCounts) -> int:
    """Actual cost of a response (``usage`` from the API), rounded up to whole micro-USD."""
    return _micro(
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_creation_input_tokens * price.cache_write
        + usage.cache_read_input_tokens * price.cache_read
    )


def estimate_input_tokens(body_len: int) -> int:
    """No count_tokens offline: about 4 bytes per token of request body."""
    return math.ceil(max(0, body_len) / BYTES_PER_TOKEN)


def reserve_usd_micro(price: Price, body_len: int, max_tokens: int, reserve_output: int) -> int:
    """Upfront hold: the whole body at the input price plus min(max_tokens, cap) output."""
    output = max(0, min(max_tokens, reserve_output))
    return _micro(estimate_input_tokens(body_len) * price.input + output * price.output)


def usd_to_micro(usd: str | Decimal) -> int:
    """'2.00' -> 2_000_000 (rounded down to whole micro-USD). Raises ValueError."""
    try:
        value = Decimal(str(usd).strip())
    except InvalidOperation:
        raise ValueError("budget must be a decimal USD amount") from None
    if not value.is_finite() or value <= 0:
        raise ValueError("budget must be a positive USD amount")
    return int(value * 1_000_000)


def format_usd(usd_micro: int) -> str:
    return f"${Decimal(usd_micro) / 1_000_000:.2f}"
