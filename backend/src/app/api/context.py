"""What routes get from the composition root, plus the request middleware.

Routes see ports and plain config only (ARCH-02); `app.main` builds this from Settings.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import FastAPI, Request, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.problems import problem
from app.domain.analytics import AnalyticsService
from app.domain.identity import Allowlist
from app.domain.ports import Clock, Ids, RateLimiter, TokenVerifier

_CORRELATION = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


@dataclass(frozen=True)
class ApiConfig:
    ip_salt: bytes
    trusted_proxy_hops: int
    max_body_bytes: int
    rate_limit_scale: float = 1.0


@dataclass(frozen=True)
class Services:
    config: ApiConfig
    allowlist: Allowlist
    verifier: TokenVerifier
    rate_limiter: RateLimiter
    clock: Clock
    ids: Ids
    analytics: AnalyticsService


def services(request: Request) -> Services:
    found = request.app.state.services
    assert isinstance(found, Services)
    return found


def client_ip(request: Request, trusted_hops: int) -> str:
    """The address `trusted_hops` proxies in front of us saw. Never trust more than that."""
    if trusted_hops > 0:
        forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",")]
        forwarded = [part for part in forwarded if part]
        if len(forwarded) >= trusted_hops:
            return forwarded[-trusted_hops]
    return request.client.host if request.client else "unknown"


def ip_key(request: Request, config: ApiConfig) -> str:
    """`ip:<hmac>`: a salted hash, never the raw address."""
    digest = hmac.new(
        config.ip_salt, client_ip(request, config.trusted_proxy_hops).encode(), hashlib.sha256
    )
    return "ip:" + digest.hexdigest()[:32]


_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


def install_middleware(app: FastAPI, ids: Ids, max_body_bytes: int) -> None:
    @app.middleware("http")
    async def _correlate(  # pyright: ignore[reportUnusedFunction]
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-correlation-id", "")
        request.state.correlation_id = (
            incoming if _CORRELATION.match(incoming) else ids.new_correlation_id()
        )
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = request.state.correlation_id
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    app.add_middleware(BodyLimit, max_bytes=max_body_bytes)


# Base64 file uploads get their own ceilings: the largest `data` the spec allows plus room for the
# rest of the JSON body. The domain refuses anything over the decoded size limit with a 413.
_UPLOAD_JSON_OVERHEAD = 1024
UPLOAD_LIMITS: tuple[tuple[str, re.Pattern[str], int], ...] = (
    ("PUT", re.compile(r"/api/profile/documents/[a-z_]{1,32}"), 6_990_508 + _UPLOAD_JSON_OVERHEAD),
    ("POST", re.compile(r"/api/contacts/import"), 2_796_204 + _UPLOAD_JSON_OVERHEAD),
)


class BodyLimit:
    """Refuse bodies over `max_bytes` (declared or streamed) with a 413 problem."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self._app = app
        self._max = max_bytes

    def _limit(self, scope: Scope) -> int:
        method, path = str(scope.get("method", "")), str(scope.get("path", ""))
        for wanted, pattern, limit in UPLOAD_LIMITS:
            if method == wanted and pattern.fullmatch(path):
                return max(self._max, limit)
        return self._max

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        limit = self._limit(scope)
        declared = dict(scope.get("headers", [])).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            await self._reject(scope, receive, send)
            return
        seen = 0

        async def limited() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise _TooLarge
            return message

        try:
            await self._app(scope, limited, send)
        except _TooLarge:
            await self._reject(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        request.state.correlation_id = "body-limit"
        await problem(request, "payload-too-large")(scope, receive, send)


class _TooLarge(Exception):
    pass
