"""Per-task bearer tokens for the llm-proxy.

Format: ``jrt1.<payload>.<sig>``. ``payload`` is base64url (no padding) of compact JSON
``{"t": task_id, "exp": unix_seconds, "b": token_budget}``; ``sig`` is base64url of
HMAC-SHA256(secret, "jrt1." + payload). The secret is local (``LLM_PROXY_SECRET``) and never
enters the sandbox; the sandbox only holds the minted token, which is useless after ``exp``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass

from .jsonval import as_int, as_object

PREFIX = "jrt1"
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_TTL_S = 24 * 3600
MIN_SECRET_LEN = 32


class TokenError(Exception):
    """The token is missing, malformed, forged or expired. ``reason`` is safe to log."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Claims:
    task_id: str
    exp: int
    budget: int


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]*", text):
        raise TokenError("malformed")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def check_secret(secret: bytes) -> bytes:
    if len(secret) < MIN_SECRET_LEN:
        raise ValueError(f"LLM_PROXY_SECRET must be at least {MIN_SECRET_LEN} bytes")
    return secret


def _sign(secret: bytes, signing_input: str) -> str:
    return _b64e(hmac.new(secret, signing_input.encode("ascii"), hashlib.sha256).digest())


def mint(secret: bytes, task_id: str, ttl_s: int, budget: int, *, now: float | None = None) -> str:
    """Mint a token. ``ttl_s`` may be negative only to build expired tokens in tests."""
    check_secret(secret)
    if not TASK_ID_RE.match(task_id):
        raise ValueError("task_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}")
    if ttl_s > MAX_TTL_S:
        raise ValueError(f"ttl_s must be <= {MAX_TTL_S}")
    if budget <= 0:
        raise ValueError("budget must be positive")
    issued = time.time() if now is None else now
    claims = {"t": task_id, "exp": int(issued) + ttl_s, "b": budget}
    payload = _b64e(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{PREFIX}.{payload}"
    return f"{signing_input}.{_sign(secret, signing_input)}"


def verify(secret: bytes, token: str | None, *, now: float | None = None) -> Claims:
    if not token:
        raise TokenError("missing")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise TokenError("malformed")
    signing_input = f"{parts[0]}.{parts[1]}"
    if not hmac.compare_digest(_sign(secret, signing_input), parts[2]):
        raise TokenError("bad_signature")
    try:
        raw = as_object(json.loads(_b64d(parts[1])))
    except (ValueError, UnicodeDecodeError) as exc:
        raise TokenError("malformed") from exc
    if raw is None:
        raise TokenError("malformed")
    task_id, exp, budget = raw.get("t"), as_int(raw.get("exp")), as_int(raw.get("b"))
    if not isinstance(task_id, str) or not TASK_ID_RE.match(task_id):
        raise TokenError("malformed")
    if exp is None or budget is None or budget <= 0:
        raise TokenError("malformed")
    current = time.time() if now is None else now
    if exp <= current:
        raise TokenError("expired")
    if exp - current > MAX_TTL_S + 60:
        raise TokenError("ttl_too_long")
    return Claims(task_id=task_id, exp=exp, budget=budget)
