"""The output redaction filter (SEC-06, ARCH-30).

Order: secret patterns, then the runtime denylist, then PII (SDP), then the content guard.
Matches become `[redacted]`.

Streaming (PLAN "Streaming + redaction"):
- Fast path, every chunk: secret patterns and the denylist run over a sliding window (the
  released tail plus the pending text), so a value split across chunks is still caught.
- Segment path: PII and the output guard run once per sentence or paragraph, and only then is
  the segment released. If the window later shows that a released segment held part of a
  secret, a `retract` event replaces that segment with `[redacted]`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.domain.errors import DomainError
from app.domain.ports import PII, ContentGuard
from app.guards.patterns import OPEN_PRIVATE_KEY, PRIVATE_KEY_START, SECRET_PATTERNS

REDACTED = "[redacted]"
WINDOW_CHARS = 256
MAX_SEGMENT_CHARS = 600
MAX_HELD_CHARS = 8192
MIN_DENYLIST_CHARS = 4

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None


class RedactionReason(StrEnum):
    CREDENTIAL = "secret"
    POLICY = "policy"
    PII = "pii"


@dataclass(frozen=True)
class Match:
    start: int
    end: int
    reason: RedactionReason


def merge(matches: Sequence[Match]) -> list[Match]:
    """Merge overlapping spans. The earliest-listed reason wins for a merged span."""
    merged: list[Match] = []
    for match in sorted(matches, key=lambda item: (item.start, -item.end)):
        if merged and match.start < merged[-1].end:
            last = merged[-1]
            merged[-1] = Match(last.start, max(last.end, match.end), last.reason)
        else:
            merged.append(match)
    return merged


def apply(text: str, matches: Sequence[Match]) -> str:
    pieces: list[str] = []
    cursor = 0
    for match in merge(matches):
        pieces.extend((text[cursor : match.start], REDACTED))
        cursor = match.end
    pieces.append(text[cursor:])
    return "".join(pieces)


class Redactor:
    def __init__(
        self,
        denylist: Sequence[str] = (),
        pii: PII | None = None,
        guard: ContentGuard | None = None,
    ) -> None:
        entries = sorted({value for value in denylist if len(value) >= MIN_DENYLIST_CHARS}, key=len)
        alternation = "|".join(re.escape(value) for value in reversed(entries))
        self._denylist = re.compile(alternation, re.IGNORECASE) if alternation else None
        self._pii = pii
        self._guard = guard

    def fast_matches(self, text: str, *, is_complete: bool = False) -> list[Match]:
        found = [
            Match(hit.start(), hit.end(), RedactionReason.CREDENTIAL)
            for pattern in SECRET_PATTERNS
            for hit in pattern.regex.finditer(text)
        ]
        if is_complete:
            found.extend(
                Match(hit.start(), hit.end(), RedactionReason.CREDENTIAL)
                for hit in OPEN_PRIVATE_KEY.finditer(text)
            )
        if self._denylist is not None:
            found.extend(
                Match(hit.start(), hit.end(), RedactionReason.POLICY)
                for hit in self._denylist.finditer(text)
            )
        return merge(found)

    def redact_fast(self, text: str, *, is_complete: bool = False) -> str:
        return apply(text, self.fast_matches(text, is_complete=is_complete))

    async def redact(self, text: str) -> str:
        """Full filter for a complete payload. Raises if PII inspection or the guard fails."""
        cleaned = self.redact_fast(text, is_complete=True)
        if self._pii is not None:
            spans = await self._pii.find(cleaned)
            cleaned = apply(
                cleaned, [Match(span.start, span.end, RedactionReason.PII) for span in spans]
            )
        if self._guard is not None and cleaned:
            verdict = await self._guard.inspect_output(cleaned)
            if verdict.is_blocked:
                return REDACTED
        return cleaned

    async def redact_or_none(self, text: str) -> str | None:
        """For agent steps: fail closed. `None` means "show the step type only"."""
        try:
            return await self.redact(text)
        except DomainError:
            return None

    async def redact_json(self, value: JsonValue) -> JsonValue:
        if isinstance(value, str):
            return await self.redact(value)
        if isinstance(value, list):
            return [await self.redact_json(item) for item in value]
        if isinstance(value, dict):
            return await self._redact_mapping(value)
        return value

    async def _redact_mapping(self, value: Mapping[str, JsonValue]) -> JsonValue:
        return {key: await self.redact_json(item) for key, item in value.items()}


@dataclass(frozen=True)
class SegmentReleased:
    segment_id: int
    text: str


@dataclass(frozen=True)
class SegmentRetracted:
    segment_id: int
    reason: RedactionReason


type StreamEvent = SegmentReleased | SegmentRetracted

_BOUNDARY = re.compile(r"(?<=[.!?:;])\s+|\n\s*\n\s*")


@dataclass
class _Segment:
    segment_id: int
    text: str
    is_retracted: bool = False


class StreamingRedactor:
    """Feed model deltas in, get `segment` / `retract` events out. One per stream."""

    def __init__(self, redactor: Redactor, *, window: int = WINDOW_CHARS) -> None:
        self._redactor = redactor
        self._window = window
        self._segments: list[_Segment] = []
        self._pending = ""

    @property
    def visible_text(self) -> str:
        """What a client that applied every event would now show."""
        return "".join(REDACTED if seg.is_retracted else seg.text for seg in self._segments)

    async def feed(self, chunk: str) -> list[StreamEvent]:
        self._pending += chunk
        events: list[StreamEvent] = list(self._retract_straddling())
        self._pending = self._redactor.redact_fast(self._pending)
        cut = self._release_point()
        if cut > 0:
            ready, self._pending = self._pending[:cut], self._pending[cut:]
            events.extend(await self._release(ready))
        return events

    async def finish(self) -> list[StreamEvent]:
        events: list[StreamEvent] = list(self._retract_straddling())
        ready = self._redactor.redact_fast(self._pending, is_complete=True)
        self._pending = ""
        if ready:
            events.extend(await self._release(ready, split=False))
        return events

    def _tail(self) -> tuple[list[_Segment], str]:
        chosen: list[_Segment] = []
        size = 0
        for segment in reversed(self._segments):
            if size >= self._window:
                break
            chosen.insert(0, segment)
            size += len(segment.text)
        return chosen, "".join(segment.text for segment in chosen)

    def _retract_straddling(self) -> list[SegmentRetracted]:
        segments, tail = self._tail()
        if not segments:
            return []
        events: list[SegmentRetracted] = []
        cut_pending_to = 0
        for match in self._redactor.fast_matches(tail + self._pending):
            if match.start >= len(tail):
                continue
            events.extend(self._retract_overlapping(segments, match))
            cut_pending_to = max(cut_pending_to, match.end - len(tail))
        if cut_pending_to > 0:
            self._pending = REDACTED + self._pending[cut_pending_to:]
        return events

    def _retract_overlapping(
        self, segments: Sequence[_Segment], match: Match
    ) -> list[SegmentRetracted]:
        events: list[SegmentRetracted] = []
        offset = 0
        for segment in segments:
            start, end = offset, offset + len(segment.text)
            offset = end
            overlaps = start < match.end and match.start < end
            if overlaps and not segment.is_retracted and segment.text != REDACTED:
                segment.is_retracted = True
                segment.text = REDACTED
                events.append(SegmentRetracted(segment.segment_id, match.reason))
        return events

    def _release_point(self) -> int:
        """End of the last complete segment in `pending`, or 0 to hold everything."""
        held = self._pending
        key_start = PRIVATE_KEY_START.search(held)
        if key_start is not None and len(held) < MAX_HELD_CHARS:
            # An open key block: release nothing past its start until the END line arrives.
            held = held[: key_start.start()]
        boundaries = [hit.end() for hit in _BOUNDARY.finditer(held)]
        if boundaries:
            return boundaries[-1]
        if len(held) > MAX_SEGMENT_CHARS:
            space = held.rfind(" ", 0, MAX_SEGMENT_CHARS)
            return space + 1 if space > 0 else MAX_SEGMENT_CHARS
        return 0

    async def _release(self, text: str, *, split: bool = True) -> list[StreamEvent]:
        pieces = _split_segments(text) if split else [text]
        events: list[StreamEvent] = []
        for piece in pieces:
            checked = await self._redactor.redact_or_none(piece)
            segment = _Segment(len(self._segments) + 1, REDACTED if checked is None else checked)
            self._segments.append(segment)
            events.append(SegmentReleased(segment.segment_id, segment.text))
        return events


def _split_segments(text: str) -> list[str]:
    pieces: list[str] = []
    cursor = 0
    for hit in _BOUNDARY.finditer(text):
        pieces.append(text[cursor : hit.end()])
        cursor = hit.end()
    if cursor < len(text):
        pieces.append(text[cursor:])
    return [piece for piece in pieces if piece]
