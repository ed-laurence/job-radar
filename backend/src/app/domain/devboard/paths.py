"""Repo paths for leases, scope checks and risk (PLAN "Agent write scope", "Collision rules").

Leases are concrete paths or directory prefixes (a trailing `/`), never globs, so overlap is
exact: two paths overlap when one's segments are a prefix of the other's.
"""

from __future__ import annotations

import re

from app.domain.devboard.model import Risk, max_risk

MAX_PATH_CHARS = 300
_GLOB = re.compile(r"[*?\[\]{}]")
_SAFE = re.compile(r"^[A-Za-z0-9._/@+-]+$")


class InvalidPath(ValueError):
    pass


def normalise(raw: str) -> str:
    """`a/b/` for a directory prefix, `a/b.py` for a file. Raises `InvalidPath`."""
    path = raw.strip().replace("\\", "/")
    if not path or len(path) > MAX_PATH_CHARS:
        raise InvalidPath("path is empty or too long")
    if _GLOB.search(path) or not _SAFE.match(path):
        raise InvalidPath("paths are concrete: no globs or special characters")
    if path.startswith("/"):
        raise InvalidPath("paths are relative to the repo root")
    is_dir = path.endswith("/")
    parts = path.rstrip("/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise InvalidPath("paths have no empty, '.' or '..' segments")
    return "/".join(parts) + ("/" if is_dir else "")


def segments(path: str) -> tuple[str, ...]:
    return tuple(path.rstrip("/").split("/"))


def overlaps(first: str, second: str) -> bool:
    left, right = segments(first), segments(second)
    shorter = min(len(left), len(right))
    return left[:shorter] == right[:shorter]


def contains(prefix: str, path: str) -> bool:
    """`path` is `prefix` itself or inside it."""
    outer, inner = segments(prefix), segments(path)
    return len(inner) >= len(outer) and inner[: len(outer)] == outer


# ---------------------------------------------------------------- write scope (allowlist)
WRITE_ALLOWLIST: tuple[str, ...] = (
    "web/src/",
    "web/tests/",
    "backend/src/app/api/",
    "backend/src/app/domain/",
    "backend/tests/unit/",
    "backend/tests/property/",
    "backend/tests/contract/",
    "backend/tests/integration/",
    "api/openapi.yaml",
    "docs/features/",
)
CONTRACT = "api/openapi.yaml"
# Code generated from the contract. Only `scripts/gen-api.sh` writes it, so it is leased
# together with the contract (`is_leasable`) but never hand-writable (`is_writable`).
REGENERATED: tuple[str, ...] = ("backend/src/app/generated/", "web/src/lib/api/generated/")
# Inside the allowlist but still never writable: auth/config modules and generated code.
_PROTECTED_INSIDE: tuple[str, ...] = (
    "backend/src/app/api/security.py",
    "backend/src/app/domain/identity.py",
    *REGENERATED,
)
# Where tests live; a test file there has a test-style name (`is_test_file`).
TEST_DIRS: tuple[str, ...] = (
    "backend/tests/unit/",
    "backend/tests/property/",
    "backend/tests/contract/",
    "backend/tests/integration/",
    "web/tests/",
)
# Web unit tests sit next to their component.
_COLOCATED_TESTS = "web/src/"
_TEST_NAME = re.compile(r"^(test_[^/]+\.py|[^/]+_test\.py|[^/]+\.(test|spec)\.[cm]?[jt]sx?)$")
_COLOCATED_NAME = re.compile(r"^[^/]+\.test\.tsx?$")
_TEST_INFRA_NAMES = frozenset(
    {"conftest.py", "pytest.ini", "sitecustomize.py", "package.json", "pyproject.toml"}
)
_TEST_INFRA_PATTERN = re.compile(r"(^|/)(vitest|playwright)\.config\.[a-z]+$|\.pth$|(^|/)setup\.")


def is_test_infrastructure(path: str) -> bool:
    name = segments(path)[-1]
    return name in _TEST_INFRA_NAMES or bool(_TEST_INFRA_PATTERN.search(path))


def is_writable(path: str) -> bool:
    """True only for paths an agent may change (an allowlist, not a denylist)."""
    if is_test_infrastructure(path):
        return False
    # A directory prefix that merely contains a protected file is fine: the Publisher checks
    # every changed path against this same function.
    if any(contains(protected, path) for protected in _PROTECTED_INSIDE):
        return False
    return any(contains(allowed, path) for allowed in WRITE_ALLOWLIST)


def is_regenerated(path: str) -> bool:
    return any(contains(prefix, path) for prefix in REGENERATED)


def is_leasable(path: str) -> bool:
    """Writable, or regenerated code (leased alongside the contract, never hand-edited)."""
    return is_writable(path) or is_regenerated(path)


def is_test_file(path: str) -> bool:
    """A test-style file name inside the test dirs (or a co-located web test); never test
    infrastructure, never a directory."""
    if path.endswith("/") or is_test_infrastructure(path) or not is_writable(path):
        return False
    name = segments(path)[-1]
    if any(contains(folder, path) for folder in TEST_DIRS):
        return bool(_TEST_NAME.match(name))
    return contains(_COLOCATED_TESTS, path) and bool(_COLOCATED_NAME.match(name))


def out_of_scope_paths(paths: tuple[str, ...]) -> list[str]:
    return [path for path in paths if not is_leasable(path)]


# ---------------------------------------------------------------- hotspots and risk
HOTSPOTS: tuple[str, ...] = (
    "api/openapi.yaml",
    "backend/src/app/generated/",
    "web/src/lib/",
)
# Risk per path, most specific entry first to win. The contract and its generated code are
# medium: they are hotspot-locked, planned as their own subtask and covered by contract tests.
# Shared web code (`web/src/lib/`) stays high: every feature imports it.
RISK_MAP: tuple[tuple[str, Risk], ...] = (
    ("api/openapi.yaml", Risk.MEDIUM),
    ("backend/src/app/generated/", Risk.MEDIUM),
    ("web/src/lib/api/generated/", Risk.MEDIUM),
    ("web/src/lib/", Risk.HIGH),
    ("backend/src/app/domain/", Risk.MEDIUM),
    ("backend/src/app/api/", Risk.MEDIUM),
    ("web/src/app/", Risk.MEDIUM),
    ("web/src/features/", Risk.LOW),
    ("web/src/components/", Risk.LOW),
    ("web/tests/", Risk.LOW),
    ("backend/tests/", Risk.LOW),
    ("docs/features/", Risk.LOW),
)


def hotspot_for(path: str) -> str | None:
    """The hotspot a path falls in (or that a directory prefix contains)."""
    return next((spot for spot in HOTSPOTS if overlaps(spot, path)), None)


def path_risk(path: str) -> Risk:
    """Most specific entry wins; unknown in-scope paths are medium; a directory prefix takes the
    highest risk of anything inside it (`web/src/` contains `web/src/lib/`, so it is high)."""
    inside = [risk for prefix, risk in RISK_MAP if contains(path, prefix) and prefix != path]
    if Risk.HIGH in inside:
        return Risk.HIGH
    matches = [(len(segments(prefix)), risk) for prefix, risk in RISK_MAP if contains(prefix, path)]
    if not matches:
        return Risk.MEDIUM
    return max(matches, key=lambda match: match[0])[1]


def code_risk(paths: tuple[str, ...]) -> Risk:
    return max_risk(*(path_risk(path) for path in paths)) if paths else Risk.LOW
