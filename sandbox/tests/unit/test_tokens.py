import base64
import hashlib
import hmac
import json

import pytest
from llm_proxy.tokens import MAX_TTL_S, TokenError, mint, verify

SECRET = b"s" * 32
NOW = 1_800_000_000.0


def test_round_trip_carries_task_expiry_and_budget() -> None:
    token = mint(SECRET, "T-42", 600, 1234, now=NOW)
    claims = verify(SECRET, token, now=NOW + 1)
    assert (claims.task_id, claims.exp, claims.usd_micro) == ("T-42", int(NOW) + 600, 1234)


def test_jrt2_format_is_stable_for_mirroring() -> None:
    """The exact bytes other minters (the backend) must reproduce."""
    token = mint(SECRET, "T-42", 600, 2_000_000, now=NOW)
    prefix, payload, sig = token.split(".")
    assert prefix == "jrt2"
    raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    assert raw == b'{"exp":1800000600,"t":"T-42","usd_micro":2000000}'
    expected = hmac.new(SECRET, f"jrt2.{payload}".encode(), hashlib.sha256).digest()
    assert sig == base64.urlsafe_b64encode(expected).rstrip(b"=").decode()
    assert "=" not in token


def test_signed_jrt1_token_is_refused_as_legacy() -> None:
    payload = base64.urlsafe_b64encode(b'{"b":200000,"exp":1800000600,"t":"T-1"}')
    p64 = payload.rstrip(b"=").decode()
    sig = hmac.new(SECRET, f"jrt1.{p64}".encode(), hashlib.sha256).digest()
    token = f"jrt1.{p64}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"
    with pytest.raises(TokenError) as err:
        verify(SECRET, token, now=NOW)
    assert err.value.reason == "legacy_token"


@pytest.mark.parametrize("token", [None, ""])
def test_missing(token: str | None) -> None:
    with pytest.raises(TokenError) as err:
        verify(SECRET, token, now=NOW)
    assert err.value.reason == "missing"


def test_expired() -> None:
    token = mint(SECRET, "T-1", 60, 10, now=NOW)
    with pytest.raises(TokenError) as err:
        verify(SECRET, token, now=NOW + 61)
    assert err.value.reason == "expired"


def test_wrong_secret_is_rejected() -> None:
    token = mint(SECRET, "T-1", 60, 10, now=NOW)
    with pytest.raises(TokenError) as err:
        verify(b"x" * 32, token, now=NOW)
    assert err.value.reason == "bad_signature"


def test_tampered_payload_is_rejected() -> None:
    prefix, _, sig = mint(SECRET, "T-1", 60, 10, now=NOW).split(".")
    forged = {"t": "T-1", "exp": int(NOW) + 60, "usd_micro": 10**9}
    payload = base64.urlsafe_b64encode(json.dumps(forged).encode()).rstrip(b"=").decode()
    with pytest.raises(TokenError) as err:
        verify(SECRET, f"{prefix}.{payload}.{sig}", now=NOW)
    assert err.value.reason == "bad_signature"


@pytest.mark.parametrize(
    "token", ["abc", "jrt0.a.b", "jrt2.a", "jrt2.a.b.c", "jrt1.a.b", "Bearer x"]
)
def test_malformed(token: str) -> None:
    with pytest.raises(TokenError):
        verify(SECRET, token, now=NOW)


def test_mint_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        mint(b"short", "T-1", 60, 10)
    with pytest.raises(ValueError):
        mint(SECRET, "../etc", 60, 10)
    with pytest.raises(ValueError):
        mint(SECRET, "T-1", MAX_TTL_S + 1, 10)
    with pytest.raises(ValueError):
        mint(SECRET, "T-1", 60, 0)
