"""Paths an agent names must be in the repo (no invented files or folders).

A path is known when it is an existing file, an existing directory (with or without the
trailing '/'), or a new file whose directory exists. Pure: the file set comes from the
`RepoFiles` port.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.domain.devboard.paths import REGENERATED, WRITE_ALLOWLIST, contains

NODE_SEPARATOR = "::"


def criterion_file(test_id: str) -> str:
    """`path/to/test_x.py::name` -> `path/to/test_x.py`."""
    return test_id.split(NODE_SEPARATOR, 1)[0]


def _dirs(files: frozenset[str]) -> frozenset[str]:
    dirs: set[str] = set()
    for file in files:
        parts = file.split("/")[:-1]
        for end in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:end]))
    return frozenset(dirs)


def _is_known(raw: str, files: frozenset[str], dirs: frozenset[str]) -> bool:
    path = raw.strip()
    path = path.removeprefix("./")
    if not path or path.startswith("/") or ".." in path.split("/"):
        return False
    if path.endswith("/"):
        return path.rstrip("/") in dirs
    if path in files or path in dirs:
        return True
    parent = path.rpartition("/")[0]
    return parent in dirs


def unknown_paths(paths: Iterable[str], files: frozenset[str]) -> tuple[str, ...]:
    """The paths that are not in the repo, each once, in the order given."""
    dirs = _dirs(files)
    unknown: list[str] = []
    for path in paths:
        if path not in unknown and not _is_known(path, files, dirs):
            unknown.append(path)
    return tuple(unknown)


def repo_listing(files: frozenset[str]) -> str:
    """The files an agent may change (inside the write scope, not generated), one per line."""
    shown = (
        file
        for file in files
        if any(contains(allowed, file) for allowed in WRITE_ALLOWLIST)
        and not any(contains(prefix, file) for prefix in REGENERATED)
    )
    return "\n".join(sorted(shown))
