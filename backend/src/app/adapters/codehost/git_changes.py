"""Parse git's diff output into the Publisher's `ChangeSet` (pure; no git calls here).

- `git diff --name-status -z -M`: per file the status letter and path(s); renames carry both.
- `git diff --numstat -z -M`: added and deleted line counts (`-` for binary files).
- `git diff -U0 -M`: the added lines. Its file sections come in the same order as the
  name-status entries (one `diff --git` header per file), so each section is matched by
  position, never by parsing a possibly quoted path.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.ports.devboard import ChangeSet, FileChange

_TWO_PATHS = frozenset({"R", "C"})
_HEADER = "diff --git "


class ChangeParseError(ValueError):
    """git's output didn't have the expected shape."""


@dataclass(frozen=True)
class _Entry:
    status: str
    path: str
    old_path: str | None


def _tokens(raw: str) -> list[str]:
    parts = raw.split("\0")
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def name_status(raw: str) -> list[_Entry]:
    tokens = _tokens(raw)
    entries: list[_Entry] = []
    index = 0
    while index < len(tokens):
        letter = tokens[index][:1]
        if letter in _TWO_PATHS:
            if index + 2 >= len(tokens):
                raise ChangeParseError("truncated rename entry")
            old, new = tokens[index + 1], tokens[index + 2]
            # A copy (C) adds a file; only a rename (R) removes its old path.
            is_rename = letter == "R"
            entries.append(_Entry("R" if is_rename else "A", new, old if is_rename else None))
            index += 3
            continue
        if index + 1 >= len(tokens):
            raise ChangeParseError("truncated name-status entry")
        # T (type change) counts as a modification.
        entries.append(_Entry("M" if letter == "T" else letter, tokens[index + 1], None))
        index += 2
    return entries


def _count(text: str) -> int:
    return int(text) if text.isdigit() else 0


def numstat(raw: str) -> dict[str, tuple[int, int]]:
    """Line counts by (new) path."""
    tokens = _tokens(raw)
    counts: dict[str, tuple[int, int]] = {}
    index = 0
    while index < len(tokens):
        added, deleted, path = [*tokens[index].split("\t", 2), "", ""][:3]
        if path:
            counts[path] = (_count(added), _count(deleted))
            index += 1
            continue
        if index + 2 >= len(tokens):
            raise ChangeParseError("truncated numstat rename")
        counts[tokens[index + 2]] = (_count(added), _count(deleted))
        index += 3
    return counts


def added_lines(patch: str) -> list[list[str]]:
    """Per file section (in order), the text of every added line."""
    sections: list[list[str]] = []
    in_hunk = False
    for line in patch.split("\n"):
        if line.startswith(_HEADER):
            sections.append([])
            in_hunk = False
        elif line.startswith("@@"):
            in_hunk = bool(sections)
        elif in_hunk and line.startswith("+"):
            sections[-1].append(line[1:].removesuffix("\r"))
    return sections


def change_set(status_raw: str, numstat_raw: str, patch: str) -> ChangeSet:
    entries = name_status(status_raw)
    counts = numstat(numstat_raw)
    sections = added_lines(patch)
    if len(sections) != len(entries):
        raise ChangeParseError("diff sections don't match the changed files")
    files = tuple(
        FileChange(
            path=entry.path,
            status=entry.status,
            added=counts.get(entry.path, (0, 0))[0],
            deleted=counts.get(entry.path, (0, 0))[1],
            old_path=entry.old_path,
        )
        for entry in entries
    )
    lines = tuple(
        (entry.path, text)
        for entry, section in zip(entries, sections, strict=True)
        for text in section
    )
    return ChangeSet(files=files, added_lines=lines)
