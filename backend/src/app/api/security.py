"""Auth and rate-limit dependencies (SEC-01/02, SEC-08).

`Authorization: Bearer <token>` carries the user's token (Identity Platform JWT, or
`fake:<email>` when AUTH=fake). `X-Serverless-Authorization` belongs to Cloud Run's
service-to-service check and is never read here.
"""

# No `from __future__ import annotations`: FastAPI must evaluate the closure's `Depends(...)`.

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request, Response

from app.api.context import Services, ip_key, services
from app.domain.errors import RateLimited, Unauthenticated
from app.domain.identity import AppRole, Principal, authorize, require_role
from app.domain.rate_limits import BucketScope, RateDecision, RateTier, policy_for, tighter

_MAX_TOKEN = 8192


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > _MAX_TOKEN or " " in token:
        raise Unauthenticated("Sign in again.")
    return token


async def current_principal(request: Request) -> Principal:
    svc = services(request)
    identity = await svc.verifier.verify(_bearer(request))
    principal = authorize(identity, svc.allowlist)
    request.state.principal = principal
    return principal


async def maintainer(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
    require_role(principal, AppRole.MAINTAINER)
    return principal


def _headers(response: Response, decision: RateDecision) -> None:
    response.headers["RateLimit-Limit"] = str(decision.limit)
    response.headers["RateLimit-Remaining"] = str(decision.remaining)
    response.headers["RateLimit-Reset"] = str(decision.reset_s)


def _deny(decision: RateDecision) -> None:
    if not decision.is_allowed:
        raise RateLimited("Slow down and try again shortly.", retry_after_s=decision.retry_after_s)


async def _by_ip(request: Request, svc: Services, tier: RateTier) -> RateDecision:
    policy = policy_for(BucketScope.IP, tier, svc.config.rate_limit_scale)
    return await svc.rate_limiter.consume(ip_key(request, svc.config), tier, policy)


def rate_limited_user(
    tier: RateTier,
    principal_dep: Callable[..., Awaitable[Principal]] = current_principal,
) -> Callable[..., Awaitable[Principal]]:
    """Authenticate, then take a token from both the per-IP and the per-user bucket."""

    async def dependency(
        request: Request,
        response: Response,
        principal: Annotated[Principal, Depends(principal_dep)],
    ) -> Principal:
        svc = services(request)
        by_ip = await _by_ip(request, svc, tier)
        user_policy = policy_for(BucketScope.USER, tier, svc.config.rate_limit_scale)
        by_user = await svc.rate_limiter.consume(f"user:{principal.user_id}", tier, user_policy)
        decision = tighter(by_ip, by_user)
        _deny(decision)
        _headers(response, decision)
        return principal

    return dependency


async def rate_limited_public(request: Request, response: Response) -> None:
    decision = await _by_ip(request, services(request), RateTier.PUBLIC)
    _deny(decision)
    _headers(response, decision)
