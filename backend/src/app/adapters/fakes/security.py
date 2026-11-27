"""Fakes for TokenVerifier, ContentGuard, PII and RateLimiter (offline dev and tests)."""

from __future__ import annotations

import hashlib
import re

from app.domain.errors import Unauthenticated
from app.domain.identity import VerifiedIdentity
from app.domain.ports import Clock, GuardAction, GuardVerdict, PiiSpan
from app.domain.rate_limits import RateDecision, RateTier, TierPolicy, available_tokens, decide

# Not a credential: Settings refuses AUTH=fake in prod.
FAKE_TOKEN_PREFIX = "fake:"
_UNVERIFIED_SUFFIX = ";unverified"


class FakeTokenVerifier:
    """`Authorization: Bearer fake:<email>` (append `;unverified` to simulate an unverified
    address). The result still goes through the real allowlist check."""

    async def verify(self, token: str) -> VerifiedIdentity:
        if not token.startswith(FAKE_TOKEN_PREFIX):
            raise Unauthenticated("Sign in again.")
        body = token.removeprefix(FAKE_TOKEN_PREFIX)
        is_verified = not body.endswith(_UNVERIFIED_SUFFIX)
        email = body.removesuffix(_UNVERIFIED_SUFFIX)
        if not email:
            raise Unauthenticated("Sign in again.")
        return VerifiedIdentity(email=email, email_verified=is_verified, subject=f"fake-{email}")


def fake_token(email: str, *, is_verified: bool = True) -> str:
    return FAKE_TOKEN_PREFIX + email + ("" if is_verified else _UNVERIFIED_SUFFIX)


INPUT_BLOCK_MARKERS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "[[guard:block]]",
)
OUTPUT_BLOCK_MARKERS = ("[[guard:block-output]]",)


class FakeContentGuard:
    """Blocks a few well-known injection phrases and explicit test markers."""

    def __init__(self) -> None:
        self.inspected_inputs: list[str] = []
        self.inspected_outputs: list[str] = []

    async def inspect_input(self, text: str) -> GuardVerdict:
        self.inspected_inputs.append(text)
        return _verdict(text, INPUT_BLOCK_MARKERS, "prompt_injection")

    async def inspect_output(self, text: str) -> GuardVerdict:
        self.inspected_outputs.append(text)
        return _verdict(text, OUTPUT_BLOCK_MARKERS, "unsafe_output")


def _verdict(text: str, markers: tuple[str, ...], category: str) -> GuardVerdict:
    lowered = text.lower()
    if any(marker in lowered for marker in markers):
        return GuardVerdict(action=GuardAction.BLOCK, categories=(category,))
    return GuardVerdict(action=GuardAction.ALLOWED)


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\w])\+?\d[\d -]{7,14}\d(?![\w])")
_TOKEN = re.compile(r"PII_(EMAIL_ADDRESS|PHONE_NUMBER)_[0-9a-f]{10}")
_DETECTORS = (("EMAIL_ADDRESS", _EMAIL), ("PHONE_NUMBER", _PHONE))
# A year range ("2019 - 2021", "2019-2021") has a phone's digits and dashes but isn't one.
_YEAR_RANGE = re.compile(r"(?:19|20)\d{2}\s*-\s*(?:19|20)\d{2}")


def _is_pii(info_type: str, value: str) -> bool:
    return not (info_type == "PHONE_NUMBER" and _YEAR_RANGE.fullmatch(value))


class FakePII:
    """Deterministic tokenisation of emails and phone numbers, like SDP's crypto-deterministic
    transform. Tokens contain no whitespace, so callers can safely split text on spaces."""

    def __init__(self) -> None:
        self._originals: dict[str, str] = {}

    async def deidentify(self, text: str) -> str:
        for info_type, pattern in _DETECTORS:
            text = pattern.sub(lambda match, kind=info_type: self._replace(kind, match[0]), text)
        return text

    async def reidentify(self, text: str) -> str:
        return _TOKEN.sub(lambda match: self._originals.get(match[0], match[0]), text)

    async def find(self, text: str) -> list[PiiSpan]:
        spans = [
            PiiSpan(start=match.start(), end=match.end(), info_type=info_type)
            for info_type, pattern in _DETECTORS
            for match in pattern.finditer(text)
            if _is_pii(info_type, match[0])
        ]
        return sorted(spans, key=lambda span: (span.start, span.end))

    def _replace(self, info_type: str, value: str) -> str:
        return self._tokenise(info_type, value) if _is_pii(info_type, value) else value

    def _tokenise(self, info_type: str, value: str) -> str:
        digest = hashlib.sha256(f"{info_type}:{value}".encode()).hexdigest()[:10]
        token = f"PII_{info_type}_{digest}"
        self._originals[token] = value
        return token


class InMemoryRateLimiter:
    """Same token-bucket maths as the Postgres adapter, per process."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._buckets: dict[tuple[str, RateTier], tuple[float, float]] = {}

    async def consume(self, key: str, tier: RateTier, policy: TierPolicy) -> RateDecision:
        now = self._clock.now().timestamp()
        tokens, refilled_at = self._buckets.get((key, tier), (float(policy.capacity), now))
        balance, decision = decide(available_tokens(tokens, now - refilled_at, policy), policy)
        self._buckets[(key, tier)] = (balance, now)
        return decision
