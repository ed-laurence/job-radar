"""Typed domain errors (ARCH-10). `app.api.errors` maps each one to an RFC 9457 problem.

Messages are safe to show: they never carry SQL, hostnames, stack traces or user data.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class FieldError:
    """One field-level problem. `pointer` is a JSON Pointer into the request body."""

    pointer: str
    message: str


class DomainError(Exception):
    """Base for every error the API layer knows how to map."""

    code: str = "internal"

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail)
        self.detail = detail


class BadRequest(DomainError):
    code = "bad-request"


class Unauthenticated(DomainError):
    code = "unauthenticated"


class Forbidden(DomainError):
    """Never says why (unknown account, unverified email or role too low)."""

    code = "forbidden"


class NotFound(DomainError):
    code = "not-found"


class Conflict(DomainError):
    code = "version-conflict"

    def __init__(self, detail: str = "", *, current_version: int | None = None) -> None:
        super().__init__(detail)
        self.current_version = current_version


class IllegalTransition(DomainError):
    code = "illegal-transition"

    def __init__(self, detail: str = "", *, allowed: Sequence[str] = ()) -> None:
        super().__init__(detail)
        self.allowed = tuple(allowed)


class PlanNotApproved(DomainError):
    code = "plan-not-approved"


class ValidationFailed(DomainError):
    code = "validation-failed"

    def __init__(self, detail: str = "", *, errors: Sequence[FieldError] = ()) -> None:
        super().__init__(detail)
        self.errors = tuple(errors)


class GuardBlocked(DomainError):
    """A content guard refused the input or output. Fail closed."""

    code = "guard-blocked"


class PayloadTooLarge(DomainError):
    code = "payload-too-large"


class RateLimited(DomainError):
    code = "rate-limited"

    def __init__(self, detail: str = "", *, retry_after_s: int = 1) -> None:
        super().__init__(detail)
        self.retry_after_s = retry_after_s


class BudgetExceeded(DomainError):
    """The daily spend cap for an LLM feature was reached."""

    code = "budget-exceeded"


class UpstreamUnavailable(DomainError):
    """A dependency (model provider, guard service) failed after retries and fallback."""

    code = "upstream-unavailable"


class NotImplementedYet(DomainError):
    """The operation is in the contract but not built yet (foundation stubs)."""

    code = "not-implemented"
