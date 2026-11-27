"""`USD_TO_AUD`: the rate budgets and spend are shown at. Defaults to 1.55 and must be > 0."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.settings import Settings


def test_default_rate() -> None:
    assert Settings(environment="test").usd_to_aud == 1.55


def test_rate_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USD_TO_AUD", "1.6")
    assert Settings(environment="test").usd_to_aud == 1.6


@pytest.mark.parametrize("rate", [0, -1.5])
def test_rate_must_be_positive(rate: float) -> None:
    with pytest.raises(ValidationError):
        Settings(environment="test", usd_to_aud=rate)
