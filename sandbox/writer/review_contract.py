"""The ``/out/review.json`` contract of a review run (stdlib only).

The backend parses this file strictly (unknown keys rejected, every key present), so the
reviewer's ``submit_review`` tool validates the model's answer here before anything is written,
and returns every problem as a tool error the model can fix. ``validate`` fills the optional
fields so the file always carries every key::

    {"task_id", "summary" (<= 80 words),
     "findings": [{"severity": BLOCKER|NIT, "rubric_id": "ARCH-03" | null,
                   "test_id": "path::name" | null, "repro": <= 60 words | null,
                   "path": a file this branch changed, "line": int >= 1 | null,
                   "evidence": <= 60 words, "fix": <= 60 words | null}] (<= 10)}

The changed files come from git in the sandbox (``git diff --name-only -z <base>..HEAD``,
written by the entrypoint before the agent starts; ``read_changed``). Whether a BLOCKER's
citation holds (a real rubric id, a test id or a repro) is the backend's call: an uncited
BLOCKER is downgraded to a NIT there.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from plan_contract import Checker, show

TOP_KEYS = ("task_id", "summary", "findings")
FINDING_KEYS = ("severity", "rubric_id", "test_id", "repro", "path", "line", "evidence", "fix")
SEVERITIES = ("BLOCKER", "NIT")
MAX_FINDINGS = 10
SUMMARY_WORDS, FINDING_WORDS = 80, 60
MAX_SUMMARY_CHARS, MAX_TEXT_CHARS, MAX_ID_CHARS = 800, 600, 300
MAX_LINE = 1_000_000
MAX_ERRORS = 15
RUBRIC_ID = re.compile(r"^[A-Z]{2,10}-[0-9]{1,3}$")
# The backend's own path rule (``review_file``): plain repo paths only.
SAFE_PATH = re.compile(r"^[A-Za-z0-9._/@+-]+$")


class ReviewError(Exception):
    """The review breaks the contract; ``errors`` are short, fixable messages."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)[:MAX_ERRORS]
        super().__init__("review refused: " + "; ".join(self.errors))


def read_changed(listing: Path) -> frozenset[str]:
    """The files the branch changed, from the entrypoint's ``git diff --name-only -z``."""
    names = listing.read_bytes().decode("utf-8", errors="replace").split("\0")
    return frozenset(name for name in names if name)


def _words(c: Checker, where: str, value: object, words: int, chars: int) -> str:
    text = c.text(where, value, chars)
    if text and len(text.split()) > words:
        c.err(f"{where} is longer than {words} words")
        return ""
    return text


def _optional_words(c: Checker, where: str, value: object) -> str | None:
    if value is None:
        return None
    return _words(c, where, value, FINDING_WORDS, MAX_TEXT_CHARS) or None


def _line(c: Checker, where: str, value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= MAX_LINE:
        c.err(f"{where} must be a line number >= 1 or null")
        return None
    return value


def _rubric(c: Checker, where: str, value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not RUBRIC_ID.match(value):
        c.err(f"{where} must be a rubric id like ARCH-03 (from review-rubric.yaml) or null")
        return None
    return value


def _test_id(c: Checker, where: str, value: object) -> str | None:
    if value is None:
        return None
    return c.text(where, value, MAX_ID_CHARS) or None


def _path(c: Checker, where: str, value: object, changed: frozenset[str] | None) -> str:
    path = c.text(where, value, MAX_ID_CHARS)
    if not path:
        return ""
    rel = c.rel_path(where, path)
    if rel is None:
        return ""
    if not SAFE_PATH.match(rel):
        c.err(f"{where}: {show(rel)} must be a plain repo path")
        return ""
    if changed is not None and rel not in changed:
        c.err(f"{where}: {show(rel)} is not a file this branch changed (see git diff --stat)")
        return ""
    return rel


def _finding(
    c: Checker, index: int, value: object, changed: frozenset[str] | None
) -> dict[str, object]:
    where = f"findings[{index}]"
    if not isinstance(value, dict):
        c.err(f"{where} must be an object")
        return {}
    raw = cast("dict[str, object]", value)
    c.keys(where, raw, FINDING_KEYS)
    severity = raw.get("severity")
    if severity not in SEVERITIES:
        c.err(f"{where}.severity must be one of {', '.join(SEVERITIES)}")
    return {
        "severity": severity if severity in SEVERITIES else "",
        "rubric_id": _rubric(c, f"{where}.rubric_id", raw.get("rubric_id")),
        "test_id": _test_id(c, f"{where}.test_id", raw.get("test_id")),
        "repro": _optional_words(c, f"{where}.repro", raw.get("repro")),
        "path": _path(c, f"{where}.path", raw.get("path"), changed),
        "line": _line(c, f"{where}.line", raw.get("line")),
        "evidence": _words(
            c, f"{where}.evidence", raw.get("evidence"), FINDING_WORDS, MAX_TEXT_CHARS
        ),
        "fix": _optional_words(c, f"{where}.fix", raw.get("fix")),
    }


def _findings(c: Checker, value: object, changed: frozenset[str] | None) -> list[object]:
    if not isinstance(value, list):
        c.err("findings must be a list of objects (may be empty)")
        return []
    items = cast("list[object]", value)
    if len(items) > MAX_FINDINGS:
        c.err(f"findings: at most {MAX_FINDINGS} (has {len(items)}); keep the most important")
        return []
    return [_finding(c, i, item, changed) for i, item in enumerate(items)]


def validate(raw: object, task_id: str, changed: frozenset[str] | None = None) -> dict[str, object]:
    """The review in contract shape (optional fields filled), or ``ReviewError``."""
    c = Checker(None)
    if not isinstance(raw, dict):
        raise ReviewError(["the review must be a JSON object"])
    data = cast("dict[str, object]", raw)
    c.keys("review", data, TOP_KEYS)
    if data.get("task_id", task_id) != task_id:
        c.err(f"task_id must be {task_id}")
    review: dict[str, object] = {
        "task_id": task_id,
        "summary": _words(c, "summary", data.get("summary"), SUMMARY_WORDS, MAX_SUMMARY_CHARS),
        "findings": _findings(c, data.get("findings"), changed),
    }
    if c.errors:
        raise ReviewError(c.errors)
    return review


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def summary(review: Mapping[str, object]) -> str:
    """One line from the counts only (the free text is untrusted)."""
    raw = review.get("findings")
    findings = cast("list[object]", raw) if isinstance(raw, list) else []
    blockers = sum(
        1
        for item in findings
        if isinstance(item, dict) and cast("dict[str, object]", item).get("severity") == "BLOCKER"
    )
    nits = len(findings) - blockers
    return (
        f"{_plural(len(findings), 'finding')} ({_plural(blockers, 'blocker')},"
        f" {_plural(nits, 'nit')})"
    )


def write(review: Mapping[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(review, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
