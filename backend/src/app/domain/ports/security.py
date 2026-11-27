"""Auth, guard, PII and rate-limit ports (SEC-01, SEC-04, SEC-05, SEC-08)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from app.domain.identity import VerifiedIdentity
from app.domain.rate_limits import RateDecision, RateTier, TierPolicy


class TokenVerifier(Protocol):
    async def verify(self, token: str) -> VerifiedIdentity:
        """Prove who sent `token`. Raises `Unauthenticated` for anything not provably valid."""
        ...


class GuardAction(StrEnum):
    ALLOWED = "pass"
    BLOCK = "block"
    REDACT = "redact"


@dataclass(frozen=True)
class GuardVerdict:
    action: GuardAction
    categories: tuple[str, ...] = field(default=())

    @property
    def is_blocked(self) -> bool:
        return self.action is GuardAction.BLOCK


class ContentGuard(Protocol):
    """Model Armor in prod. Implementations fail closed: errors raise, never pass."""

    async def inspect_input(self, text: str) -> GuardVerdict: ...

    async def inspect_output(self, text: str) -> GuardVerdict: ...


@dataclass(frozen=True)
class PiiSpan:
    start: int
    end: int
    info_type: str


class PII(Protocol):
    """Sensitive Data Protection in prod. De-identification is deterministic and reversible."""

    async def deidentify(self, text: str) -> str: ...

    async def reidentify(self, text: str) -> str: ...

    async def find(self, text: str) -> list[PiiSpan]: ...


class RateLimiter(Protocol):
    async def consume(self, key: str, tier: RateTier, policy: TierPolicy) -> RateDecision: ...
