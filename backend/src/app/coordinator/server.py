"""The coordinator's HTTP API (internal; not part of `api/openapi.yaml`). Schema:
`docs/features/coordinator.md`.

    GET  /healthz              {"status": "ok"} (no token)
    GET  /v1/task?cursor=N     brief, acceptance criteria, plan, lease paths, budget (paged)
    POST /v1/leases/claim      {paths, wait_s <= 60}: blocks server-side; {granted, waiting}
    POST /v1/leases/renew      {}
    POST /v1/leases/release    {paths?}
    POST /v1/steps             {kind, text <= 2000 chars}
    POST /v1/handoff           the AGENTS.md handoff object
    POST /v1/help              {text <= 200 tokens}: moves the task to Blocked

Every `/v1` call: body cap (413), bearer `jrc1` token (401 when missing, malformed, badly
signed, expired or for another audience), run check (403 unless the token's run is an active run
of its task), strict JSON (422; 413 for a message over its cap, refused and never cut). Response
bodies pass the redaction filter (SEC-06) and are capped at 2k tokens. Errors are RFC 9457
problem+json with a code and a correlation id only: no SQL, hosts or traces (SEC-07).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from sqlalchemy.exc import SQLAlchemyError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.coordinator.config import CoordinatorSettings
from app.coordinator.schemas import (
    MAX_BODY_BYTES,
    MAX_CURSOR,
    RESULT_TOKENS,
    ClaimRequest,
    HelpRequest,
    MessageTooLarge,
    ReleaseRequest,
    RenewRequest,
    StepRequest,
    parse_capped,
)
from app.coordinator.service import CoordinatorService, Json, Scope
from app.coordinator.task_pages import ResultTooLarge
from app.coordinator.tokens import TokenRefused, verify
from app.coordinator.wiring import CoordinatorRuntime, build_runtime
from app.domain.devboard.messages import estimate_tokens
from app.domain.devboard.model import StepKind
from app.domain.errors import (
    Conflict,
    DomainError,
    Forbidden,
    IllegalTransition,
    NotFound,
    Unauthenticated,
    ValidationFailed,
)

PROBLEM_MEDIA_TYPE = "application/problem+json"
_log = logging.getLogger("app.coordinator")
# Infrastructure failures: answered as "unavailable", logged by type only (never the message).
_UNAVAILABLE = (SQLAlchemyError, OSError, TimeoutError)

# Server-generated values (ids, enums, numbers, timestamps): never user text, so not redacted.
_STRUCTURAL_KEYS = frozenset(
    {
        "id",
        "kind",
        "column",
        "type",
        "size",
        "risk",
        "areas",
        "status",
        "round",
        "mode",
        "version",
        "approved",
        "expires_at",
    }
)


class _TooLarge(DomainError):
    code = "too-large"


class _Unavailable(DomainError):
    code = "unavailable"


_STATUS: tuple[tuple[type[DomainError], int, str], ...] = (
    (Unauthenticated, 401, "Unauthenticated"),
    (Forbidden, 403, "Forbidden"),
    (NotFound, 404, "Not found"),
    (Conflict, 409, "Conflict"),
    (IllegalTransition, 409, "Illegal transition"),
    (MessageTooLarge, 413, "Message too large"),
    (_TooLarge, 413, "Payload too large"),
    (ResultTooLarge, 422, "Result too large"),
    (ValidationFailed, 422, "Validation failed"),
    (_Unavailable, 503, "Unavailable"),
)


def problem(error: DomainError, path: str) -> JSONResponse:
    status, title = next(
        ((status, title) for kind, status, title in _STATUS if isinstance(error, kind)),
        (500, "Internal error"),
    )
    correlation = uuid.uuid4().hex
    body: dict[str, object] = {
        "type": f"/problems/{error.code}",
        "title": title,
        "status": status,
        "code": error.code,
        "correlation_id": correlation,
        "instance": path[:200],
    }
    if error.detail:
        body["detail"] = error.detail[:1000]
    if isinstance(error, ValidationFailed) and error.errors:
        body["errors"] = [{"pointer": e.pointer, "message": e.message} for e in error.errors]
    headers = {"X-Correlation-Id": correlation}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA_TYPE, headers=headers)


async def _redact(runtime: CoordinatorRuntime, value: Json, key: str | None = None) -> Json:
    if key in _STRUCTURAL_KEYS:
        return value
    if isinstance(value, str):
        return await runtime.redactor.redact(value)
    if isinstance(value, list):
        return [await _redact(runtime, item, key) for item in value]
    if isinstance(value, dict):
        return {name: await _redact(runtime, item, name) for name, item in value.items()}
    return value


# ------------------------------------------------------------------ request handling
async def _body(request: Request) -> bytes:
    """The whole body, refused (never cut) past the cap, with or without Content-Length."""
    length = request.headers.get("content-length")
    if length is not None and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
        raise _TooLarge("The request body is over the size cap.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise _TooLarge("The request body is over the size cap.")
        chunks.append(chunk)
    return b"".join(chunks)


async def _authorize(runtime: CoordinatorRuntime, request: Request) -> Scope:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Unauthenticated("A coordinator token is required.")
    try:
        grant = verify(runtime.key, token.strip(), now=int(runtime.clock.now().timestamp()))
    except TokenRefused as refused:
        _log.info("coordinator token refused: %s", refused.reason)
        raise Unauthenticated("The coordinator token was refused.") from None
    return await runtime.service.authorize(grant)


type _Handler = Callable[[CoordinatorService, Scope, Request, bytes], Awaitable[dict[str, Json]]]
type _Endpoint = Callable[[Request], Awaitable[JSONResponse]]


def _endpoint(runtime: CoordinatorRuntime, handler: _Handler) -> _Endpoint:
    async def endpoint(request: Request) -> JSONResponse:
        try:
            body = await _body(request)
            scope = await _authorize(runtime, request)
            payload = await handler(runtime.service, scope, request, body)
            clean = await _redact(runtime, payload)
            if estimate_tokens(json.dumps(clean, ensure_ascii=False)) > RESULT_TOKENS:
                raise ResultTooLarge("The response is over the 2k-token cap.")
            return JSONResponse(clean)
        except DomainError as error:
            return problem(error, request.url.path)
        except _UNAVAILABLE as error:
            _log.warning("coordinator %s failed: %s", request.url.path, type(error).__name__)
            return problem(_Unavailable("Try again later."), request.url.path)

    return endpoint


def _no_body(body: bytes) -> None:
    if body.strip():
        raise ValidationFailed("This endpoint takes no body.")


def _cursor(request: Request) -> int:
    extra = set(request.query_params) - {"cursor"}
    raw = request.query_params.get("cursor", "0")
    if extra or not raw.isdigit() or int(raw) > MAX_CURSOR:
        raise ValidationFailed("Only `cursor` (a non-negative page offset) is accepted.")
    return int(raw)


async def _task(
    service: CoordinatorService, scope: Scope, request: Request, body: bytes
) -> dict[str, Json]:
    _no_body(body)
    return await service.get_task(scope, _cursor(request))


async def _claim(
    service: CoordinatorService, scope: Scope, _request: Request, body: bytes
) -> dict[str, Json]:
    claim = parse_capped(ClaimRequest, body)
    return await service.claim_paths(scope, tuple(claim.paths), claim.wait_s)


async def _renew(
    service: CoordinatorService, scope: Scope, _request: Request, body: bytes
) -> dict[str, Json]:
    parse_capped(RenewRequest, body or b"{}")
    return await service.renew_lease(scope)


async def _release(
    service: CoordinatorService, scope: Scope, _request: Request, body: bytes
) -> dict[str, Json]:
    release = parse_capped(ReleaseRequest, body or b"{}")
    paths = None if release.paths is None else tuple(release.paths)
    return await service.release_paths(scope, paths)


async def _step(
    service: CoordinatorService, scope: Scope, _request: Request, body: bytes
) -> dict[str, Json]:
    step = parse_capped(StepRequest, body)
    return await service.post_step(scope, StepKind(step.kind), step.text)


async def _handoff(
    service: CoordinatorService, scope: Scope, _request: Request, body: bytes
) -> dict[str, Json]:
    return await service.post_handoff(scope, body)


async def _help(
    service: CoordinatorService, scope: Scope, _request: Request, body: bytes
) -> dict[str, Json]:
    return await service.request_help(scope, parse_capped(HelpRequest, body).text)


async def _healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


ROUTES: tuple[tuple[str, str, _Handler], ...] = (
    ("GET", "/v1/task", _task),
    ("POST", "/v1/leases/claim", _claim),
    ("POST", "/v1/leases/renew", _renew),
    ("POST", "/v1/leases/release", _release),
    ("POST", "/v1/steps", _step),
    ("POST", "/v1/handoff", _handoff),
    ("POST", "/v1/help", _help),
)


def build_app(runtime: CoordinatorRuntime) -> Starlette:
    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncGenerator[None]:
        await runtime.start()
        try:
            yield
        finally:
            await runtime.aclose()

    routes = [Route("/healthz", _healthz, methods=["GET"])]
    routes += [
        Route(path, _endpoint(runtime, handler), methods=[method])
        for method, path, handler in ROUTES
    ]
    hosts = [host.split(":", 1)[0] for host in runtime.allowed_hosts]
    return Starlette(
        routes=routes,
        middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=hosts)],
        lifespan=lifespan,
    )


def create_app(settings: CoordinatorSettings | None = None) -> Starlette:
    """App factory: `uvicorn app.coordinator.server:create_app --factory`."""
    return build_app(build_runtime(settings if settings is not None else CoordinatorSettings()))
