"""Who may use the app (SEC-01/02): an exact, lower-cased allowlist match on every request.

There is no sign-up. The allowlist (`APP_USERS`) is config, never code.
"""

from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.domain.errors import Forbidden

# Stable namespace so a user's id is the same in every process and environment
# without a database lookup per request. The seed loader and provisioning use it too.
USER_ID_NAMESPACE = uuid.UUID("6f1c1d9e-3a52-5b7e-9c43-0b9d2f6a7e10")

_MAX_EMAIL = 254


class AppRole(StrEnum):
    MAINTAINER = "maintainer"
    USER = "user"


_ROLE_RANK = {AppRole.USER: 0, AppRole.MAINTAINER: 1}


def role_satisfies(actual: AppRole, required: AppRole) -> bool:
    return _ROLE_RANK[actual] >= _ROLE_RANK[required]


@dataclass(frozen=True)
class VerifiedIdentity:
    """What a token verifier proved about the caller. Nothing here is trusted yet."""

    email: str
    email_verified: bool
    subject: str


@dataclass(frozen=True)
class AllowlistEntry:
    email: str
    role: AppRole
    user_id: uuid.UUID
    display_name: str | None = None


@dataclass(frozen=True)
class Principal:
    """An authenticated, allowlisted caller."""

    user_id: uuid.UUID
    email: str
    role: AppRole
    display_name: str | None = None

    @property
    def is_maintainer(self) -> bool:
        return self.role is AppRole.MAINTAINER


def normalise_email(raw: str) -> str:
    """Lower-case only. No Unicode folding, trimming or dot/plus stripping: lookalikes must fail."""
    return raw.lower()


def is_plain_ascii_email(value: str) -> bool:
    if not value or len(value) > _MAX_EMAIL or not value.isascii():
        return False
    if any(unicodedata.category(char).startswith(("C", "Z")) for char in value):
        return False
    local, sep, domain = value.partition("@")
    return bool(sep and local and domain and "@" not in domain and "." in domain)


def derive_user_id(email: str) -> uuid.UUID:
    return uuid.uuid5(USER_ID_NAMESPACE, normalise_email(email))


class Allowlist:
    """Exact-match allowlist keyed by the lower-cased email."""

    def __init__(self, entries: Iterable[AllowlistEntry]) -> None:
        by_email: dict[str, AllowlistEntry] = {}
        for entry in entries:
            key = normalise_email(entry.email)
            if not is_plain_ascii_email(key):
                raise ValueError("allowlist entries must be plain ASCII email addresses")
            if key in by_email:
                raise ValueError("duplicate allowlist entry")
            by_email[key] = entry
        self._by_email: Mapping[str, AllowlistEntry] = by_email

    @property
    def entries(self) -> tuple[AllowlistEntry, ...]:
        return tuple(self._by_email.values())

    def lookup(self, email: str) -> AllowlistEntry | None:
        # Dict lookup is exact: "user@example.test " or a Cyrillic "е" never matches.
        return self._by_email.get(normalise_email(email))

    def entry_for_role(self, role: AppRole) -> AllowlistEntry | None:
        return next((entry for entry in self._by_email.values() if entry.role is role), None)


def authorize(identity: VerifiedIdentity, allowlist: Allowlist) -> Principal:
    """Turn a verified identity into a principal, or refuse with a generic `Forbidden`."""
    refusal = Forbidden("This account can't sign in.")
    if not identity.email_verified:
        raise refusal
    entry = allowlist.lookup(identity.email)
    if entry is None:
        raise refusal
    return Principal(
        user_id=entry.user_id,
        email=normalise_email(entry.email),
        role=entry.role,
        display_name=entry.display_name,
    )


def require_role(principal: Principal, required: AppRole) -> None:
    if not role_satisfies(principal.role, required):
        raise Forbidden("This account can't do that.")
