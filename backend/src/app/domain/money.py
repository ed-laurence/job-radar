"""Money shown to users is AUD. Providers bill in USD, so `llm_calls.cost_usd` stays in USD
and is converted here at the deployment's `USD_TO_AUD` rate. Budgets are AUD throughout:
they are compared with converted spend, never with raw USD."""

from __future__ import annotations

DEFAULT_USD_TO_AUD = 1.55


def to_aud(usd: float, usd_to_aud: float) -> float:
    """`usd` in AUD, rounded to six decimal places like the stored costs."""
    if usd_to_aud <= 0:
        raise ValueError("usd_to_aud must be greater than 0")
    return round(usd * usd_to_aud, 6)
