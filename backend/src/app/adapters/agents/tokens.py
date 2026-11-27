"""Per-run credentials for the sandbox: the llm-proxy token and the coordinator token.

The coordinator token comes from `app.coordinator.tokens` (the coordinator owns its format).

The llm-proxy formats are reimplemented here (stdlib only); docs/features/dev-agents.md
"llm-proxy tokens" is the contract the proxy verifies against:
- `jrt1.<b64url{"t":task_id,"exp":unix,"b":tokens}>.<b64url HMAC-SHA256(secret, "jrt1.<payload>")>`
  (token budget; kept for the proxy's CLI mint and older callers).
- `jrt2.<b64url{"t":task_id,"exp":unix,"usd_micro":int}>.<b64url HMAC-SHA256(secret,
  "jrt2.<payload>")>` (USD budget in micro-dollars; what the dev runner mints).
Payload JSON is compact with sorted keys; b64url has no padding. Error messages never carry the
secret or a token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import uuid
from collections.abc import Callable
from typing import Protocol

from app.coordinator import tokens as coordinator_tokens

PREFIX = "jrt1"
PREFIX_USD = "jrt2"
MICRO_PER_USD = 1_000_000
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_TTL_S = 24 * 3600
MIN_SECRET_LEN = 32


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _mint(
    secret: bytes,
    prefix: str,
    task_id: str,
    ttl_s: int,
    budget: tuple[str, int],
    now: float | None,
) -> str:
    if len(secret) < MIN_SECRET_LEN:
        raise ValueError(f"LLM_PROXY_SECRET must be at least {MIN_SECRET_LEN} bytes")
    if not TASK_ID_RE.match(task_id):
        raise ValueError("task_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}")
    if not 0 <= ttl_s <= MAX_TTL_S:
        raise ValueError(f"ttl_s must be within 0..{MAX_TTL_S}")
    claim, amount = budget
    if amount <= 0:
        raise ValueError("budget must be positive")
    issued = time.time() if now is None else now
    claims = {"t": task_id, "exp": int(issued) + ttl_s, claim: amount}
    payload = _b64e(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{prefix}.{payload}"
    signature = hmac.new(secret, signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64e(signature)}"


def mint_llm_proxy_token(
    secret: bytes, task_id: str, ttl_s: int, budget: int, *, now: float | None = None
) -> str:
    """`jrt1`: a budget in model tokens."""
    return _mint(secret, PREFIX, task_id, ttl_s, ("b", budget), now)


def mint_llm_proxy_budget_token(
    secret: bytes, task_id: str, ttl_s: int, usd_micro: int, *, now: float | None = None
) -> str:
    """`jrt2`: a budget in micro-USD (1 USD = 1_000_000), priced and enforced by llm-proxy."""
    return _mint(secret, PREFIX_USD, task_id, ttl_s, ("usd_micro", usd_micro), now)


def usd_to_micro(usd: float) -> int:
    """Rounded to the nearest micro-dollar, never below 1 for a positive budget."""
    if not usd > 0:
        raise ValueError("budget must be positive")
    return max(1, round(usd * MICRO_PER_USD))


class CoordinatorTokensUnavailable(Exception):
    """The coordinator token could not be minted. Safe to show."""


class CoordinatorTokens(Protocol):
    def mint(self, task_id: uuid.UUID, run_id: uuid.UUID, ttl: int) -> str:
        """A task-scoped coordinator token valid for `ttl` seconds."""
        ...


class CoordinatorTokenMint:
    """`app.coordinator.tokens.mint` with COORDINATOR_TOKEN_KEY, the key the coordinator
    verifies with. The key never leaves this object; the token goes only into the child env."""

    def __init__(self, key: bytes, *, wall: Callable[[], float] = time.time) -> None:
        self.__key = key
        self._wall = wall

    def __repr__(self) -> str:
        return "CoordinatorTokenMint()"

    def mint(self, task_id: uuid.UUID, run_id: uuid.UUID, ttl: int) -> str:
        try:
            return coordinator_tokens.mint(
                self.__key, task_id=task_id, run_id=run_id, ttl_s=ttl, now=int(self._wall())
            )
        except ValueError:
            raise CoordinatorTokensUnavailable("Could not mint the coordinator token.") from None
