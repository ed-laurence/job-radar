"""Coordinator tokens: `jrc1.<b64url payload>.<b64url HMAC-SHA256(key, "jrc1.<payload>")>`.

Any tampering, an expired token, the wrong audience or a lifetime over 24 h is refused.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.coordinator.tokens import (
    MAX_TTL_SECONDS,
    TokenRefused,
    mint,
    verify,
)

KEY = b"k" * 32
OTHER_KEY = b"o" * 32
NOW = 1_800_000_000
TASK = uuid.UUID("00000000-0000-4000-8000-0000000000aa")
RUN = uuid.UUID("00000000-0000-4000-8000-0000000000bb")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _forge(payload: object, key: bytes = KEY, *, raw: str | None = None) -> str:
    """A correctly signed token around an arbitrary payload (to test the claim checks)."""
    body = raw if raw is not None else json.dumps(payload, separators=(",", ":"))
    head = f"jrc1.{_b64(body.encode())}"
    signature = hmac.new(key, head.encode("ascii"), hashlib.sha256).digest()
    return f"{head}.{_b64(signature)}"


def _claims(**overrides: object) -> dict[str, object]:
    claims: dict[str, object] = {
        "t": str(TASK),
        "r": str(RUN),
        "exp": NOW + 600,
        "aud": "coordinator",
    }
    claims.update(overrides)
    return claims


def test_mint_then_verify_returns_the_task_and_run() -> None:
    token = mint(KEY, task_id=TASK, run_id=RUN, ttl_s=600, now=NOW)
    grant = verify(KEY, token, now=NOW)
    assert (grant.task_id, grant.run_id, grant.expires_at) == (TASK, RUN, NOW + 600)
    assert token.startswith("jrc1.")
    assert token.count(".") == 2


def test_the_payload_has_exactly_the_contract_fields() -> None:
    token = mint(KEY, task_id=TASK, run_id=RUN, ttl_s=60, now=NOW)
    payload = token.split(".")[1]
    decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert decoded == {"t": str(TASK), "r": str(RUN), "exp": NOW + 60, "aud": "coordinator"}


def test_the_signature_is_hmac_sha256_over_the_prefix_and_payload() -> None:
    token = mint(KEY, task_id=TASK, run_id=RUN, ttl_s=60, now=NOW)
    prefix, payload, signature = token.split(".")
    expected = hmac.new(KEY, f"{prefix}.{payload}".encode(), hashlib.sha256).digest()
    assert signature == _b64(expected)


def test_a_string_key_works_like_its_utf8_bytes() -> None:
    token = mint("k" * 32, task_id=TASK, run_id=RUN, ttl_s=60, now=NOW)
    assert verify(KEY, token, now=NOW).task_id == TASK


@pytest.mark.parametrize("ttl", [0, -1, MAX_TTL_SECONDS + 1])
def test_mint_refuses_a_lifetime_outside_one_second_to_24_hours(ttl: int) -> None:
    with pytest.raises(ValueError, match="ttl"):
        mint(KEY, task_id=TASK, run_id=RUN, ttl_s=ttl, now=NOW)


def test_mint_and_verify_refuse_a_short_key() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        mint(b"short", task_id=TASK, run_id=RUN, ttl_s=60, now=NOW)
    token = mint(KEY, task_id=TASK, run_id=RUN, ttl_s=60, now=NOW)
    with pytest.raises(ValueError, match="32 bytes"):
        verify(b"short", token, now=NOW)


def test_expired_tokens_are_refused_at_the_expiry_second() -> None:
    token = mint(KEY, task_id=TASK, run_id=RUN, ttl_s=60, now=NOW)
    assert verify(KEY, token, now=NOW + 59).run_id == RUN
    with pytest.raises(TokenRefused) as refused:
        verify(KEY, token, now=NOW + 60)
    assert refused.value.reason == "expired"


def test_a_token_signed_with_another_key_is_refused() -> None:
    token = mint(OTHER_KEY, task_id=TASK, run_id=RUN, ttl_s=60, now=NOW)
    with pytest.raises(TokenRefused) as refused:
        verify(KEY, token, now=NOW)
    assert refused.value.reason == "signature"


def test_the_wrong_audience_is_refused_even_when_signed() -> None:
    for aud in ("llm-proxy", "Coordinator", ""):
        with pytest.raises(TokenRefused) as refused:
            verify(KEY, _forge(_claims(aud=aud)), now=NOW)
        assert refused.value.reason == "audience"


def test_a_signed_lifetime_over_24_hours_is_refused() -> None:
    token = _forge(_claims(exp=NOW + MAX_TTL_SECONDS + 1))
    with pytest.raises(TokenRefused) as refused:
        verify(KEY, token, now=NOW)
    assert refused.value.reason == "lifetime"
    assert verify(KEY, _forge(_claims(exp=NOW + MAX_TTL_SECONDS)), now=NOW).task_id == TASK


@pytest.mark.parametrize(
    "claims",
    [
        _claims(extra="x"),
        {"t": str(TASK), "r": str(RUN), "exp": NOW + 60},
        _claims(t="not-a-uuid"),
        _claims(r=123),
        _claims(exp="1800000600"),
        _claims(exp=True),
        _claims(exp=1.8e9),
        [str(TASK), str(RUN)],
    ],
)
def test_signed_payloads_that_break_the_schema_are_refused(claims: object) -> None:
    with pytest.raises(TokenRefused) as refused:
        verify(KEY, _forge(claims), now=NOW)
    assert refused.value.reason == "malformed"


@pytest.mark.parametrize(
    "token",
    [
        "",
        "jrc1",
        "jrc1.abc",
        "jrc1.a.b.c",
        "jrt1.e30.AAAA",
        "Bearer jrc1.e30.AAAA",
        "jrc1.e30=.AAAA",
        "jrc1.e3+.AAAA",
        "jrc1." + "A" * 2000 + ".AAAA",
    ],
)
def test_malformed_tokens_are_refused(token: str) -> None:
    with pytest.raises(TokenRefused):
        verify(KEY, token, now=NOW)


def test_a_signed_payload_that_is_not_json_is_refused() -> None:
    with pytest.raises(TokenRefused) as refused:
        verify(KEY, _forge(None, raw="{not json"), now=NOW)
    assert refused.value.reason == "malformed"


def test_refusals_never_echo_the_token() -> None:
    token = mint(OTHER_KEY, task_id=TASK, run_id=RUN, ttl_s=60, now=NOW)
    with pytest.raises(TokenRefused) as refused:
        verify(KEY, token, now=NOW)
    assert token not in str(refused.value)
    assert token.split(".")[2] not in str(refused.value)


# ------------------------------------------------------------------ properties
_TOKEN = mint(KEY, task_id=TASK, run_id=RUN, ttl_s=600, now=NOW)
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.="


@given(st.integers(min_value=0, max_value=len(_TOKEN) - 1), st.sampled_from(_ALPHABET))
def test_any_single_character_change_is_refused(index: int, char: str) -> None:
    tampered = _TOKEN[:index] + char + _TOKEN[index + 1 :]
    if tampered == _TOKEN:
        return
    with pytest.raises(TokenRefused):
        verify(KEY, tampered, now=NOW)


@given(st.text(max_size=300))
def test_arbitrary_text_is_never_accepted(text: str) -> None:
    with pytest.raises(TokenRefused):
        verify(KEY, text, now=NOW)


@given(st.uuids(), st.uuids())
def test_swapping_in_another_payload_breaks_the_signature(task: uuid.UUID, run: uuid.UUID) -> None:
    other = mint(KEY, task_id=task, run_id=run, ttl_s=600, now=NOW)
    prefix, _, signature = _TOKEN.split(".")
    spliced = f"{prefix}.{other.split('.')[1]}.{signature}"
    if (task, run) == (TASK, RUN):
        assert verify(KEY, spliced, now=NOW).task_id == TASK
        return
    with pytest.raises(TokenRefused):
        verify(KEY, spliced, now=NOW)


@given(st.uuids(), st.uuids(), st.integers(min_value=1, max_value=MAX_TTL_SECONDS))
def test_every_minted_token_verifies_until_it_expires(
    task: uuid.UUID, run: uuid.UUID, ttl: int
) -> None:
    token = mint(KEY, task_id=task, run_id=run, ttl_s=ttl, now=NOW)
    grant = verify(KEY, token, now=NOW + ttl - 1)
    assert (grant.task_id, grant.run_id) == (task, run)
    with pytest.raises(TokenRefused):
        verify(KEY, token, now=NOW + ttl)
