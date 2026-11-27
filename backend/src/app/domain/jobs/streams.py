"""Typed stream events for kit, chat and follow-up SSE (ARCH-30) and the relay that redacts
model deltas.

Only text that has passed the segment filter (`Redaction` port) is ever emitted or stored.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.domain.errors import DomainError
from app.domain.jobs.ports import FilterEvent, Redaction, Released


@dataclass(frozen=True)
class Segment:
    segment_id: str
    seq: int
    text: str
    part: str | None = None


@dataclass(frozen=True)
class Retract:
    segment_id: str
    seq: int
    reason: str


@dataclass(frozen=True)
class Done:
    seq: int
    result_id: uuid.UUID | None = None
    status: str = "ok"


@dataclass(frozen=True)
class Failed:
    seq: int
    error: DomainError


type StreamEvent = Segment | Retract | Done | Failed


class SegmentRelay:
    """Numbers events across one SSE stream and keeps the visible text of each part."""

    def __init__(self, redaction: Redaction) -> None:
        self._redaction = redaction
        self._seq = 0
        self.texts: dict[str, str] = {}

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def relay(
        self, chunks: AsyncIterator[str], part: str, *, key: str | None = None
    ) -> AsyncIterator[Segment | Retract]:
        """`key` (default: `part`) prefixes segment ids and names the kept text, so one part
        can be relayed several times in a stream (one follow-up draft per target)."""
        name = key or part
        segment_filter = self._redaction.open_stream()
        async for chunk in chunks:
            for event in await segment_filter.feed(chunk):
                yield self._convert(event, part, name)
        for event in await segment_filter.finish():
            yield self._convert(event, part, name)
        self.texts[name] = segment_filter.visible_text.strip()

    def _convert(self, event: FilterEvent, part: str, name: str) -> Segment | Retract:
        segment_id = f"{name}-{event.segment_id}"
        if isinstance(event, Released):
            return Segment(segment_id=segment_id, seq=self.next_seq(), text=event.text, part=part)
        return Retract(segment_id=segment_id, seq=self.next_seq(), reason=event.reason)
