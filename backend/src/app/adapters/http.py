"""Outbound HTTP (SEC-10): adapters only, and only to allowlisted hosts. No user-supplied URLs."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Protocol, cast

import google.auth
import google.auth.credentials
import google.auth.transport.requests
import httpx

from app.domain.errors import UpstreamUnavailable

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

GOOGLE_API_HOSTS = frozenset(
    {
        "aiplatform.googleapis.com",
        "dlp.googleapis.com",
        "www.googleapis.com",
        "securetoken.googleapis.com",
        "oauth2.googleapis.com",
    }
)


class HostNotAllowed(UpstreamUnavailable):
    """An adapter tried to reach a host outside its allowlist."""


def allowed_hosts(*extra: str) -> frozenset[str]:
    return GOOGLE_API_HOSTS | frozenset(extra)


def is_host_allowed(host: str, allowed: Iterable[str]) -> bool:
    host = host.lower()
    for entry in allowed:
        if entry.startswith("*.") and host.endswith(entry[1:]):
            return True
        if host == entry:
            return True
    return False


def allowlisted_client(allowed: Iterable[str], *, timeout_s: float = 15.0) -> httpx.AsyncClient:
    """An httpx client that refuses any request to a host not in `allowed`."""
    hosts = frozenset(allowed)

    async def _check(request: httpx.Request) -> None:
        if request.url.scheme != "https" or not is_host_allowed(request.url.host, hosts):
            raise HostNotAllowed("Outbound host is not allowlisted.")

    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s),
        event_hooks={"request": [_check]},
        follow_redirects=False,
    )


class AccessTokenSource(Protocol):
    async def token(self) -> str: ...


class GoogleAccessTokens:
    """Application Default Credentials (Workload Identity on Cloud Run; no key files)."""

    def __init__(self) -> None:
        self._credentials: google.auth.credentials.Credentials | None = None
        self._lock = asyncio.Lock()

    async def token(self) -> str:
        async with self._lock:
            credentials = self._credentials
            if credentials is None:
                found = await asyncio.to_thread(_default_credentials)
                credentials = self._credentials = found
            if not credentials.valid:
                # google-auth refreshes with a blocking transport, so keep it off the loop.
                await asyncio.to_thread(_refresh, credentials)
            token = cast("str | None", credentials.token)
        if not token:
            raise UpstreamUnavailable("Cloud credentials are unavailable.")
        return token


def _default_credentials() -> google.auth.credentials.Credentials:
    found = cast(
        "tuple[google.auth.credentials.Credentials, str | None]",
        google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE]),  # pyright: ignore[reportUnknownMemberType]
    )
    return found[0]


def _refresh(credentials: google.auth.credentials.Credentials) -> None:
    credentials.refresh(google.auth.transport.requests.Request())  # pyright: ignore[reportUnknownMemberType]


class StaticAccessTokens:
    """For tests and local experiments against mocked endpoints."""

    def __init__(self, value: str = "test-access-token") -> None:
        self._value = value

    async def token(self) -> str:
        return self._value
