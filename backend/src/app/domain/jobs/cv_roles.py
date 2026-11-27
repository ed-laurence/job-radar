"""The roles on a CV, from its extracted text (pure; ported from job-seeker's CV blocks).

Only the EXPERIENCE section counts: from an all-caps heading naming experience, employment or
career history, up to the next all-caps heading (EDUCATION, SKILLS...). A role is anchored on
a line with a date range ("Mar 2024 - Present", "Jan 2009 - Mar 2016", "2019 - 2021") in one
of two layouts:

(a) a "Title" line, then "Employer | Location | Dates" (or "Employer | Dates");
(b) separate "Title", "Employer", "Location" and "Dates" lines.

Each role reads "Title, Employer". The text is untrusted data: it is only matched, never run.
"""

from __future__ import annotations

import re

MAX_LABEL = 200

_DASH = "[-" + chr(0x2013) + chr(0x2014) + "]"
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
_POINT = rf"(?:{_MONTH}\s+)?(?:19|20)\d{{2}}"
_END = rf"(?:{_POINT}|present|current|now|today)"
DATE_RANGE = re.compile(rf"\b{_POINT}\s*(?:{_DASH}|\bto\b)\s*{_END}\b", re.IGNORECASE)
_HEADING = re.compile(r"[A-Z][A-Z &/,'-]{3,59}")
_EXPERIENCE_WORDS = ("EXPERIENCE", "EMPLOYMENT", "CAREER HISTORY", "WORK HISTORY")
_BULLETS = ("-", "*", chr(0x2022), chr(0x25AA), chr(0x25E6), chr(0x2023), chr(0x25CF))


def _clean(text: str) -> str:
    return " ".join(text.split())


def _is_heading(line: str) -> bool:
    return bool(_HEADING.fullmatch(line.rstrip(":"))) and not any(c.islower() for c in line)


def _experience_lines(text: str) -> list[str]:
    """The non-blank lines of every EXPERIENCE section, in order."""
    lines: list[str] = []
    inside = False
    for raw in text.splitlines():
        line = _clean(raw)
        if not line:
            continue
        if _is_heading(line):
            inside = any(word in line.upper() for word in _EXPERIENCE_WORDS)
            continue
        if inside:
            lines.append(line)
    return lines


def _plain(line: str) -> bool:
    """A line that can be a title or an employer: not a bullet and not itself a date line."""
    return not line.startswith(_BULLETS) and DATE_RANGE.search(line) is None


def _label(title: str, employer: str) -> str | None:
    title, employer = title.strip(" ,|"), employer.strip(" ,|")
    if not title or not employer:
        return None
    label = f"{title}, {employer}"
    return label if len(label) <= MAX_LABEL else label[:MAX_LABEL].rstrip(" ,")


def cv_roles(text: str) -> list[str]:
    """ "Title, Employer" for each role in the EXPERIENCE section, in CV order, without
    duplicates."""
    lines = _experience_lines(text)
    found: list[str] = []
    for index, line in enumerate(lines):
        match = DATE_RANGE.search(line)
        if match is None:
            continue
        label: str | None = None
        before = line[: match.start()].strip()
        if "|" in before:  # (a) Employer | Location | Dates, after the title line
            employer = before.split("|", 1)[0]
            if index >= 1 and _plain(lines[index - 1]):
                label = _label(lines[index - 1], employer)
        elif not before.strip(" ,|") and index >= 3:  # (b) Title / Employer / Location / Dates
            title, employer = lines[index - 3], lines[index - 2]
            if _plain(title) and _plain(employer) and _plain(lines[index - 1]):
                label = _label(title, employer)
        if label is not None and label not in found:
            found.append(label)
    return found
