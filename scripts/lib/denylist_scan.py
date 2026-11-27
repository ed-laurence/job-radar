"""Scan files for denylisted identifiers (project id, emails, domain, bucket names...).

    git ls-files -z -co --exclude-standard | python denylist_scan.py <denylist> [--dir DIR ...] [--stdin-text]

- File paths come NUL-separated on stdin; each --dir is scanned recursively as well.
- With --stdin-text, stdin is instead treated as one text blob (e.g. a diff) labelled "<stdin>".
- With --redact, stdin text is copied to stdout with every denylisted value replaced by
  [redacted] (used before posting findings anywhere). Always exits 0 unless the denylist is unreadable.
- Matching is case-insensitive and literal. Entries shorter than 4 characters and lines starting
  with '#' are ignored.
- Matched VALUES are never printed: only the file, line number and entry index.
Exit: 0 clean, 1 hits found, 2 usage or read error (fails closed).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

MAX_BYTES = 20 * 1024 * 1024
SKIP_DIRS = {".git", "node_modules", ".venv", ".terraform", "__pycache__", ".artifacts"}


def load_denylist(path: Path) -> list[str]:
    entries = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        value = raw.strip()
        if value and not value.startswith("#") and len(value) >= 4:
            entries.append(value.casefold())
    return entries


def scan_text(label: str, text: str, entries: list[str]) -> list[tuple[str, int, int]]:
    hits = []
    for lineno, line in enumerate(text.casefold().splitlines(), start=1):
        for idx, value in enumerate(entries, start=1):
            if value in line:
                hits.append((label, lineno, idx))
    return hits


def read_file(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8192]:  # binary
        return None
    return data.decode("utf-8", errors="replace")


def iter_dir(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            yield Path(dirpath) / name


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    denylist = Path(argv[1])
    dirs: list[Path] = []
    stdin_text = False
    redact = False
    rest = argv[2:]
    while rest:
        arg = rest.pop(0)
        if arg == "--dir" and rest:
            dirs.append(Path(rest.pop(0)))
        elif arg == "--stdin-text":
            stdin_text = True
        elif arg == "--redact":
            redact = True
        else:
            print(f"unknown argument: {arg}", file=sys.stderr)
            return 2
    try:
        entries = load_denylist(denylist)
    except OSError as exc:
        print(f"cannot read denylist: {type(exc).__name__}", file=sys.stderr)
        return 2
    if not entries:
        print("denylist has no usable entries", file=sys.stderr)
        return 2

    if redact:
        text = sys.stdin.read()
        for value in sorted(entries, key=len, reverse=True):
            text = re.sub(re.escape(value), "[redacted]", text, flags=re.IGNORECASE)
        sys.stdout.write(text)
        return 0

    hits: list[tuple[str, int, int]] = []
    scanned = 0
    if stdin_text:
        hits += scan_text("<stdin>", sys.stdin.read(), entries)
        scanned += 1
    else:
        names = sys.stdin.buffer.read().split(b"\0")
        for name in filter(None, names):
            path = Path(os.fsdecode(name))
            if SKIP_DIRS.intersection(path.parts):
                continue
            text = read_file(path)
            if text is not None:
                scanned += 1
                hits += scan_text(path.as_posix(), text, entries)
    for root in dirs:
        for path in iter_dir(root):
            text = read_file(path)
            if text is not None:
                scanned += 1
                hits += scan_text(path.as_posix(), text, entries)

    print(f"scanned {scanned} text file(s) against {len(entries)} denylisted value(s)")
    for label, lineno, idx in hits:
        print(f"  {label}:{lineno}: matches denylist entry #{idx}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
