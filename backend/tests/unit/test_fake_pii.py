"""The offline PII fake (GUARDS=fake). Regression: year-only CV date ranges ("2019 - 2021") were
tokenised as phone numbers, so those roles vanished from the CV text and the experience
contexts."""

from __future__ import annotations

import pytest

from app.adapters.fakes.security import FakePII


@pytest.mark.parametrize("dates", ["2019 - 2021", "2009-2016", "1998 -  2004"])
async def test_year_only_ranges_are_not_phone_numbers(dates: str) -> None:
    pii = FakePII()
    text = f"Example Payments | Victoria | {dates}"
    assert await pii.deidentify(text) == text
    assert await pii.find(text) == []


async def test_phone_numbers_and_emails_are_still_tokenised() -> None:
    pii = FakePII()
    text = "Call 0400 000 000 or alex@example.test"
    safe = await pii.deidentify(text)
    assert "0400 000 000" not in safe
    assert "alex@example.test" not in safe
    assert [span.info_type for span in await pii.find(text)] == ["PHONE_NUMBER", "EMAIL_ADDRESS"]
    assert await pii.reidentify(safe) == text
