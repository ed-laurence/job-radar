"""Prices from ai/models.yaml and costs in micro-USD."""

from __future__ import annotations

from decimal import Decimal

import pytest
from conftest import PRICES
from llm_proxy.budget import BudgetLedger
from llm_proxy.pricing import (
    Price,
    PriceTableError,
    TokenCounts,
    cost_usd_micro,
    estimate_input_tokens,
    format_usd,
    parse_price_table,
    reserve_usd_micro,
    usd_to_micro,
)

OPUS = PRICES["claude-opus-5-5"]
TWO_USD = 2_000_000


def test_real_table_has_the_agent_models_with_positive_prices() -> None:
    for model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"):
        price = PRICES[model]
        assert price.input > 0 and price.output > 0
        assert 0 < price.cache_read < price.input  # the table's own cache_read entry
        assert price.cache_write == price.input * Decimal("1.25")  # no entry: documented 1.25x


def test_table_entries_win_and_missing_cache_prices_use_the_multipliers() -> None:
    table = parse_price_table(
        "version: 1\n"
        "# comment\n"
        "pricing:\n"
        "  a: {input: 3.00, output: 15.00, cache_read: 0.5, cache_write: 4}  # note\n"
        "  # VERIFY: something\n"
        "  org/b-maas: {input: 1, output: 2}\n"
        "features:\n"
        "  c: {input: 9, output: 9}\n"
    )
    assert table["a"] == Price(Decimal(3), Decimal(15), Decimal("0.5"), Decimal(4))
    assert table["org/b-maas"].cache_read == Decimal("0.1")
    assert table["org/b-maas"].cache_write == Decimal("1.25")
    assert "c" not in table


@pytest.mark.parametrize(
    "text",
    [
        "features:\n  x: {input: 1, output: 2}\n",  # no section
        "pricing:\n",  # empty
        "pricing: {}\n",  # flow form at the top
        "pricing:\n  a: {input: 1}\n",  # output missing
        "pricing:\n  a: {input: 1, output: x}\n",  # not a number
        "pricing:\n  a: {input: -1, output: 2}\n",  # negative
        "pricing:\n  a: {input: 1, output: 2, cahce_read: 1}\n",  # typo
        "pricing:\n  a:\n    input: 1\n",  # block form
        "pricing:\n  a: {input: 1, output: 2}\n  a: {input: 1, output: 2}\n",  # duplicate
    ],
)
def test_malformed_tables_are_refused(text: str) -> None:
    with pytest.raises(PriceTableError):
        parse_price_table(text)


def test_cost_is_tokens_times_usd_per_mtok_rounded_up() -> None:
    # Opus 4 / 20 / read 0.40 / write 5.00 USD per Mtok.
    usage = TokenCounts(1000, 100, 2000, 10_000)
    assert cost_usd_micro(OPUS, usage) == 1000 * 4 + 100 * 20 + 2000 * 5 + 10_000 * 0.4
    assert (
        cost_usd_micro(
            Price(Decimal("0.01"), Decimal(0), Decimal(0), Decimal(0)), TokenCounts(input_tokens=1)
        )
        == 1
    )


def test_cache_reads_are_cheap_ten_cached_turns_stay_well_under_two_dollars() -> None:
    """The failed run: a ~20k cached prefix re-read every turn exhausted a 200k-token budget."""
    ledger = BudgetLedger()
    body_len = 20_000 * 4  # the whole prefix is in every request body
    for turn in range(10):
        reservation = ledger.reserve(
            "T-1", TWO_USD, reserve_usd_micro(OPUS, body_len, 32_000, 8192)
        )
        usage = TokenCounts(
            input_tokens=1500,
            output_tokens=800,
            cache_creation_input_tokens=20_000 if turn == 0 else 1500,
            cache_read_input_tokens=0 if turn == 0 else 20_000,
        )
        ledger.settle(reservation, cost_usd_micro(OPUS, usage))
    used = ledger.used("T-1")
    assert used < TWO_USD // 4, format_usd(used)  # about $0.46
    assert ledger.usage("T-1").requests == 10


def test_reserve_estimates_body_over_four_and_caps_output() -> None:
    assert estimate_input_tokens(10) == 3
    assert reserve_usd_micro(OPUS, 4000, 32_000, 8192) == 1000 * 4 + 8192 * 20
    assert reserve_usd_micro(OPUS, 4000, 100, 8192) == 1000 * 4 + 100 * 20


def test_usd_conversions() -> None:
    assert usd_to_micro("2.00") == TWO_USD
    assert usd_to_micro("0.000001") == 1
    assert format_usd(1_974_321) == "$1.97"
    for bad in ("0", "-1", "abc", "NaN"):
        with pytest.raises(ValueError):
            usd_to_micro(bad)
