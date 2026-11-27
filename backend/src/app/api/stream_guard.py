"""Last-resort guard for SSE bodies (ARCH-30, SEC-07).

Once a stream has started, an exception can't become a problem+json response: it would cut the
connection mid-frame, leave the client hanging, and poison keep-alive connections behind the BFF.
This wrapper is the streaming counterpart of `problems._unexpected`: it logs the error *type*
with the correlation id (never the message, which can carry SQL, hostnames or user data), emits a
generic typed `error` event, and ends the stream cleanly. Domain errors keep their own handling
inside each stream; this only catches what escaped them.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

import structlog
from fastapi import Request

from app.api.problems import correlation_id, problem

_log = structlog.get_logger("app.api")
_EVENT_ID = re.compile(r"^id: (\d+)$", re.MULTILINE)


def internal_error_frame(request: Request, last_id: int) -> str:
    body = json.loads(bytes(problem(request, "internal").body))
    return f"event: error\nid: {last_id + 1}\ndata: {json.dumps(body, separators=(',', ':'))}\n\n"


async def guard_stream(request: Request, frames: AsyncIterator[str]) -> AsyncIterator[str]:
    last_id = 0
    try:
        async for chunk in frames:
            ids = _EVENT_ID.findall(chunk)
            if ids:
                last_id = int(ids[-1])
            yield chunk
    except Exception as error:  # stream boundary (CODE-04 exception): same contract as _unexpected
        _log.error(
            "unhandled_stream_error",
            error_type=type(error).__name__,
            correlation_id=correlation_id(request),
        )
        yield internal_error_frame(request, last_id)
