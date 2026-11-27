"""Identity Platform ID-token verification (SEC-01).

The user's token arrives in `Authorization: Bearer <jwt>`. In prod the caller's Google ID token
for service-to-service auth arrives separately in `X-Serverless-Authorization` and is checked by
Cloud Run before the request reaches us; this verifier never looks at it.
"""

from __future__ import annotations

import asyncio
from typing import cast

import jwt
from jwt import PyJWKClient

from app.domain.errors import Unauthenticated
from app.domain.identity import VerifiedIdentity

JWKS_URL = (
    "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
)
_ALGORITHMS = ["RS256"]
_LEEWAY_S = 30
_REQUIRED = ["exp", "iat", "iss", "aud", "sub", "auth_time"]


class IdentityPlatformVerifier:
    def __init__(
        self, project_id: str, *, tenant_id: str | None = None, jwks: PyJWKClient | None = None
    ) -> None:
        self._project_id = project_id
        self._tenant_id = tenant_id
        self._issuer = f"https://securetoken.google.com/{project_id}"
        self._jwks = jwks or PyJWKClient(JWKS_URL, cache_keys=True, lifespan=3600, timeout=5)

    async def verify(self, token: str) -> VerifiedIdentity:
        refusal = Unauthenticated("Sign in again.")
        try:
            # PyJWKClient fetches over blocking urllib; keep it off the event loop.
            signing_key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, token)
            claims = cast(
                "dict[str, object]",
                jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=_ALGORITHMS,
                    audience=self._project_id,
                    issuer=self._issuer,
                    leeway=_LEEWAY_S,
                    options={"require": _REQUIRED},
                ),
            )
        except (jwt.PyJWTError, OSError):
            raise refusal from None
        return self._identity(claims, refusal)

    def _identity(self, claims: dict[str, object], refusal: Unauthenticated) -> VerifiedIdentity:
        subject = claims.get("sub")
        email = claims.get("email")
        if not isinstance(subject, str) or not subject or not isinstance(email, str):
            raise refusal
        if self._tenant_id is not None:
            firebase = claims.get("firebase")
            tenant = (
                cast("dict[str, object]", firebase).get("tenant")
                if isinstance(firebase, dict)
                else None
            )
            if tenant != self._tenant_id:
                raise refusal
        return VerifiedIdentity(
            email=email, email_verified=claims.get("email_verified") is True, subject=subject
        )
