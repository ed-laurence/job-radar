"""The `jrt2` llm-proxy token: a USD budget in micro-dollars. The fixed vector below is the
contract with `sandbox/llm-proxy` (docs/features/dev-agents.md "llm-proxy tokens")."""

from __future__ import annotations

import base64
import json

import pytest

from app.adapters.agents.tokens import mint_llm_proxy_budget_token, usd_to_micro

VECTOR_SECRET = b"jrt2-vector-secret-not-real-0123456789"
VECTOR_TOKEN = (
    "jrt2.eyJleHAiOjE4MDAwMDA2MDAsInQiOiJ0YXNrLTEiLCJ1c2RfbWljcm8iOjI1MDAwMDB9"
    ".2ZmGKhbuHMcWHpT9sOGz3vqP-I9MxSTuPygJQ9iuz1g"
)


def test_mint_matches_the_fixed_vector() -> None:
    token = mint_llm_proxy_budget_token(
        VECTOR_SECRET, "task-1", 600, 2_500_000, now=1_800_000_000.9
    )
    assert token == VECTOR_TOKEN


def test_payload_is_task_expiry_and_micro_usd() -> None:
    payload = VECTOR_TOKEN.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert claims == {"t": "task-1", "exp": 1_800_000_600, "usd_micro": 2_500_000}


@pytest.mark.parametrize(
    ("usd", "micro"),
    [(2.0, 2_000_000), (0.1, 100_000), (0.000_000_4, 1), (12.345_678_9, 12_345_679)],
)
def test_usd_to_micro_rounds_and_never_reaches_zero(usd: float, micro: int) -> None:
    assert usd_to_micro(usd) == micro


@pytest.mark.parametrize(
    ("secret", "task_id", "ttl", "usd_micro"),
    [
        (b"short", "task-1", 60, 1000),
        (VECTOR_SECRET, "bad id!", 60, 1000),
        (VECTOR_SECRET, "task-1", 25 * 3600, 1000),
        (VECTOR_SECRET, "task-1", -1, 1000),
        (VECTOR_SECRET, "task-1", 60, 0),
    ],
)
def test_mint_refuses_bad_input(secret: bytes, task_id: str, ttl: int, usd_micro: int) -> None:
    with pytest.raises(ValueError, match=r".+") as caught:
        mint_llm_proxy_budget_token(secret, task_id, ttl, usd_micro)
    assert VECTOR_SECRET.decode() not in str(caught.value)


def test_usd_to_micro_refuses_non_positive() -> None:
    with pytest.raises(ValueError, match="budget"):
        usd_to_micro(0.0)
