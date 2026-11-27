"""SSE framing for kit, chat and step streams (ARCH-30). Every frame is built from a generated
event model, so the wire shape matches the spec; text has already passed the redaction filter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import cast

from fastapi import Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents.research.service import RunEnded
from app.api.problems import problem
from app.api.stream_guard import guard_stream
from app.domain.errors import DomainError
from app.domain.jobs.records import StepRecord
from app.domain.jobs.streams import Done, Failed, Retract, Segment, StreamEvent
from app.generated.models import (
    Data,
    Data1,
    Data2,
    DoneEvent,
    ErrorEvent,
    Part1,
    Problem,
    Reason,
    RetractEvent,
    SegmentEvent,
    Step,
    StepEvent,
)

_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


def frame(event: SegmentEvent | RetractEvent | DoneEvent | ErrorEvent | StepEvent) -> str:
    data = cast("BaseModel", event.data).model_dump_json(exclude_none=True)
    return f"event: {event.event}\nid: {event.id}\ndata: {data}\n\n"


def error_frame(request: Request, error: DomainError, seq: int) -> str:
    body = json.loads(bytes(problem(request, error.code, error.detail or None).body))
    return frame(ErrorEvent(event="error", id=str(seq), data=Problem.model_validate(body)))


def generation_frame(request: Request, event: StreamEvent) -> str:
    if isinstance(event, Segment):
        part = Part1(event.part) if event.part else None
        data = Data(segment_id=event.segment_id, seq=event.seq, part=part, text=event.text)
        return frame(SegmentEvent(event="segment", id=str(event.seq), data=data))
    if isinstance(event, Retract):
        retracted = Data1(
            segment_id=event.segment_id, replacement="[redacted]", reason=Reason(event.reason)
        )
        return frame(RetractEvent(event="retract", id=str(event.seq), data=retracted))
    if isinstance(event, Done):
        done = Data2(result_id=event.result_id, status=event.status)
        return frame(DoneEvent(event="done", id=str(event.seq), data=done))
    return error_frame(request, event.error, event.seq)


def generation_stream(request: Request, events: AsyncIterator[StreamEvent]) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        async for event in events:
            yield generation_frame(request, event)
            if isinstance(event, Failed):
                return

    return StreamingResponse(
        guard_stream(request, body()), media_type="text/event-stream", headers=_HEADERS
    )


def step_stream(
    request: Request, events: AsyncIterator[StepRecord | RunEnded]
) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        last = 0
        try:
            async for event in events:
                if isinstance(event, RunEnded):
                    done = Data2(result_id=event.run.id, status=event.run.status)
                    yield frame(DoneEvent(event="done", id=str(last + 1), data=done))
                    return
                last = event.seq
                step = Step.model_validate(event, from_attributes=True)
                yield frame(StepEvent(event="step", id=str(event.seq), data=step))
        except DomainError as error:
            yield error_frame(request, error, last + 1)

    return StreamingResponse(
        guard_stream(request, body()), media_type="text/event-stream", headers=_HEADERS
    )
