"""RFC 9457 problem responses (ARCH-10). Every non-2xx body has this shape."""

from __future__ import annotations

from collections.abc import Sequence

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain.errors import (
    Conflict,
    DomainError,
    FieldError,
    IllegalTransition,
    RateLimited,
    ValidationFailed,
)

PROBLEM_MEDIA_TYPE = "application/problem+json"

_STATUS: dict[str, tuple[int, str]] = {
    "bad-request": (400, "Bad request"),
    "unauthenticated": (401, "Sign in required"),
    "forbidden": (403, "Not allowed"),
    "not-found": (404, "Not found"),
    "method-not-allowed": (405, "Method not allowed"),
    "version-conflict": (409, "Changed elsewhere"),
    "scan-running": (409, "A scan is already running"),
    "payload-too-large": (413, "Request too large"),
    "validation-failed": (422, "Invalid request"),
    "illegal-transition": (422, "Move not allowed"),
    "plan-not-approved": (422, "Plan not approved"),
    "guard-blocked": (422, "Blocked by the safety filter"),
    "rate-limited": (429, "Too many requests"),
    "budget-exceeded": (429, "Daily AI budget used up"),
    "not-implemented": (501, "Not built yet"),
    "upstream-unavailable": (503, "Service unavailable"),
    "internal": (500, "Something went wrong"),
}

_log = structlog.get_logger("app.api")


def correlation_id(request: Request) -> str:
    value = getattr(request.state, "correlation_id", None)
    return value if isinstance(value, str) else "unknown"


def problem(
    request: Request,
    code: str,
    detail: str | None = None,
    *,
    extra: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    status, title = _STATUS.get(code, _STATUS["internal"])
    cid = correlation_id(request)
    body: dict[str, object] = {
        "type": f"/problems/{code}",
        "title": title,
        "status": status,
        "correlation_id": cid,
        "instance": request.url.path[:500],
    }
    if detail:
        body["detail"] = detail[:1000]
    body.update(extra or {})
    return JSONResponse(
        body,
        status_code=status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers={"X-Correlation-Id": cid, **(headers or {})},
    )


def _field_errors(errors: Sequence[FieldError]) -> list[dict[str, str]]:
    return [{"pointer": e.pointer[:200], "message": e.message[:300]} for e in errors[:50]]


def _pointer(location: Sequence[object]) -> str:
    # FastAPI prefixes with the source ("body", "query"); the pointer is into the body.
    parts = [str(part) for part in location]
    if parts and parts[0] == "body":
        parts = parts[1:]
    return "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


async def _domain_error(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, DomainError)
    extra: dict[str, object] = {}
    headers: dict[str, str] = {}
    if isinstance(error, ValidationFailed) and error.errors:
        extra["errors"] = _field_errors(error.errors)
    if isinstance(error, Conflict) and error.current_version is not None:
        extra["current_version"] = error.current_version
    if isinstance(error, IllegalTransition) and error.allowed:
        extra["allowed_columns"] = list(error.allowed)
    if isinstance(error, RateLimited):
        headers["Retry-After"] = str(error.retry_after_s)
    return problem(request, error.code, error.detail or None, extra=extra, headers=headers)


async def _validation_error(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, RequestValidationError)
    # Pydantic's messages are generic ("Field required"); never echo the submitted input.
    errors = [
        FieldError(pointer=_pointer(item.get("loc", ())), message=str(item.get("msg", "Invalid")))
        for item in error.errors()
    ]
    return problem(
        request,
        "validation-failed",
        "Check the highlighted fields.",
        extra={"errors": _field_errors(errors)},
    )


async def _http_error(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, StarletteHTTPException)
    code = {404: "not-found", 405: "method-not-allowed", 413: "payload-too-large"}.get(
        error.status_code, "bad-request"
    )
    return problem(request, code)


async def _unexpected(request: Request, error: Exception) -> JSONResponse:
    # Log the type only: messages can carry SQL, hostnames or user data.
    _log.error(
        "unhandled_error", error_type=type(error).__name__, correlation_id=correlation_id(request)
    )
    return problem(request, "internal")


def install(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected)
