"""Token-bucket rate limits (SEC-08). Tiers match the spec's `x-rate-limit-tier`."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


class RateTier(StrEnum):
    PUBLIC = "public"
    DEFAULT = "default"
    EVENTS = "events"
    STREAM = "stream"
    AI = "ai"
    AGENT = "agent"


class BucketScope(StrEnum):
    USER = "user"
    IP = "ip"


@dataclass(frozen=True)
class TierPolicy:
    """`capacity` tokens, refilled at `refill_per_minute`. One request costs one token."""

    capacity: int
    refill_per_minute: float

    @property
    def refill_per_second(self) -> float:
        return self.refill_per_minute / 60.0


# AI and agent routes are much stricter: they spend money and run long jobs.
USER_POLICIES: dict[RateTier, TierPolicy] = {
    RateTier.PUBLIC: TierPolicy(capacity=60, refill_per_minute=60),
    RateTier.DEFAULT: TierPolicy(capacity=120, refill_per_minute=120),
    RateTier.EVENTS: TierPolicy(capacity=30, refill_per_minute=30),
    RateTier.STREAM: TierPolicy(capacity=20, refill_per_minute=20),
    RateTier.AI: TierPolicy(capacity=6, refill_per_minute=10),
    RateTier.AGENT: TierPolicy(capacity=2, refill_per_minute=3),
}

# Per-IP buckets are looser than per-user ones (two accounts may share an address)
# but still cap a single source that cycles tokens.
IP_POLICIES: dict[RateTier, TierPolicy] = {
    tier: TierPolicy(capacity=policy.capacity * 2, refill_per_minute=policy.refill_per_minute * 2)
    for tier, policy in USER_POLICIES.items()
}


def policy_for(scope: BucketScope, tier: RateTier, scale: float = 1.0) -> TierPolicy:
    """`scale` > 1 loosens limits for local e2e runs only; prod is pinned to 1 by Settings."""
    table = USER_POLICIES if scope is BucketScope.USER else IP_POLICIES
    base = table[tier]
    if scale == 1.0:
        return base
    return TierPolicy(
        capacity=round(base.capacity * scale), refill_per_minute=base.refill_per_minute * scale
    )


@dataclass(frozen=True)
class RateDecision:
    is_allowed: bool
    limit: int
    remaining: int
    reset_s: int
    retry_after_s: int


def available_tokens(tokens: float, elapsed_s: float, policy: TierPolicy) -> float:
    """Tokens after refilling for `elapsed_s`, capped at capacity. Clock skew never refills."""
    return min(float(policy.capacity), tokens + max(0.0, elapsed_s) * policy.refill_per_second)


def decide(available: float, policy: TierPolicy) -> tuple[float, RateDecision]:
    """Take one token if there is one. Returns the stored balance and the decision."""
    is_allowed = available >= 1.0
    balance = available - 1.0 if is_allowed else available
    rate = policy.refill_per_second
    missing_for_next = max(0.0, 1.0 - balance)
    retry_after = 0 if is_allowed else max(1, math.ceil(missing_for_next / rate))
    reset = math.ceil((policy.capacity - balance) / rate) if balance < policy.capacity else 0
    decision = RateDecision(
        is_allowed=is_allowed,
        limit=policy.capacity,
        remaining=max(0, math.floor(balance)),
        reset_s=max(0, reset),
        retry_after_s=retry_after,
    )
    return balance, decision


def tighter(first: RateDecision, second: RateDecision) -> RateDecision:
    """Combine two bucket decisions: denied wins, then the fewer remaining tokens."""
    if first.is_allowed != second.is_allowed:
        return second if first.is_allowed else first
    if not first.is_allowed:
        return first if first.retry_after_s >= second.retry_after_s else second
    return first if first.remaining <= second.remaining else second
