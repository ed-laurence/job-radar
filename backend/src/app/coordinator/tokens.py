"""Task-scoped coordinator tokens (pure: stdlib and pydantic only, so the runner can import it).

    jrc1.<b64url payload>.<b64url HMAC-SHA256(key, "jrc1.<b64url payload>")>
    payload = {"t": task_id, "r": run_id, "exp": unix seconds, "aud": "coordinator"}

Base64url is unpadded and must be canonical. The signature is checked (constant time) before
the payload is parsed. A token is refused when it is malformed, badly signed, expired, for
another audience, or valid for more than 24 hours from now. Refusals never echo the token.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictInt, ValidationError

PREFIX = "jrc1"
AUDIENCE = "coordinator"
MAX_TTL_SECONDS = 24 * 60 * 60
MIN_KEY_BYTES = 32
MAX_TOKEN_CHARS = 1024

_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")

type RefusalReason = Literal["malformed", "signature", "expired", "audience", "lifetime"]


class TokenRefused(Exception):
    """The token can't be used. `reason` is safe to log; the token itself never is."""

    def __init__(self, reason: RefusalReason) -> None:
        super().__init__(f"coordinator token refused: {reason}")
        self.reason: RefusalReason = reason


@dataclass(frozen=True)
class Grant:
    task_id: uuid.UUID
    run_id: uuid.UUID
    expires_at: int


class _Claims(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    t: uuid.UUID
    r: uuid.UUID
    exp: StrictInt
    aud: str


def _key(key: str | bytes) -> bytes:
    raw = key.encode("utf-8") if isinstance(key, str) else key
    if len(raw) < MIN_KEY_BYTES:
        raise ValueError(f"COORDINATOR_TOKEN_KEY must be at least {MIN_KEY_BYTES} bytes")
    return raw


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode(text: str) -> bytes:
    if not _B64URL.match(text):
        raise TokenRefused("malformed")
    try:
        data = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise TokenRefused("malformed") from None
    if _encode(data) != text:
        raise TokenRefused("malformed")
    return data


def _signature(key: bytes, head: str) -> str:
    return _encode(hmac.new(key, head.encode("ascii"), hashlib.sha256).digest())


def mint(
    key: str | bytes,
    *,
    task_id: uuid.UUID,
    run_id: uuid.UUID,
    ttl_s: int,
    now: int | None = None,
) -> str:
    """A token for one task and run, valid for `ttl_s` seconds (1 s to 24 h) from `now`."""
    raw_key = _key(key)
    if not 1 <= ttl_s <= MAX_TTL_SECONDS:
        raise ValueError(f"ttl must be between 1 and {MAX_TTL_SECONDS} seconds")
    issued = int(time.time()) if now is None else now
    claims = {"t": str(task_id), "r": str(run_id), "exp": issued + ttl_s, "aud": AUDIENCE}
    payload = json.dumps(claims, separators=(",", ":")).encode("utf-8")
    head = f"{PREFIX}.{_encode(payload)}"
    return f"{head}.{_signature(raw_key, head)}"


def verify(key: str | bytes, token: str, *, now: int) -> Grant:
    """The token's grant, or `TokenRefused`."""
    raw_key = _key(key)
    if len(token) > MAX_TOKEN_CHARS:
        raise TokenRefused("malformed")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != PREFIX or not parts[1] or not parts[2]:
        raise TokenRefused("malformed")
    head = f"{parts[0]}.{parts[1]}"
    expected = _signature(raw_key, head).encode("ascii")
    if not hmac.compare_digest(expected, parts[2].encode("utf-8", errors="replace")):
        raise TokenRefused("signature")
    try:
        claims = _Claims.model_validate_json(_decode(parts[1]))
    except ValidationError:
        raise TokenRefused("malformed") from None
    if claims.aud != AUDIENCE:
        raise TokenRefused("audience")
    if claims.exp <= now:
        raise TokenRefused("expired")
    if claims.exp - now > MAX_TTL_SECONDS:
        raise TokenRefused("lifetime")
    return Grant(task_id=claims.t, run_id=claims.r, expires_at=claims.exp)
