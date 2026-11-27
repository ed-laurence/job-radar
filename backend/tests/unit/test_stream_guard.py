"""Regression (found in local integration): an unexpected error mid-SSE crashed the response,
left the client hanging and poisoned the BFF's keep-alive connection (the next request got 502).
The guard must end the stream with a generic typed `error` event and leak nothing."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.api.stream_guard import guard_stream

SECRET_DETAIL = "relation users at db-internal.example.test:5432 password=hunter2"


def _app(frames: list[str], *, fail: bool) -> FastAPI:
    app = FastAPI()

    @app.get("/stream")
    async def stream(request: Request) -> StreamingResponse:
        async def body() -> AsyncIterator[str]:
            for chunk in frames:
                yield chunk
            if fail:
                raise RuntimeError(SECRET_DETAIL)

        return StreamingResponse(guard_stream(request, body()), media_type="text/event-stream")

    return app


def _events(raw: str) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for block in raw.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append(fields)
    return events


def test_unexpected_error_ends_stream_with_generic_error_event() -> None:
    first = 'event: segment\nid: 7\ndata: {"text":"hello"}\n\n'
    response = TestClient(_app([first], fail=True)).get("/stream")

    assert response.status_code == 200
    events = _events(response.text)
    assert [event["event"] for event in events] == ["segment", "error"]
    assert events[-1]["id"] == "8"  # continues the client's sequence for Last-Event-ID
    problem = json.loads(events[-1]["data"])
    assert problem["status"] == 500
    assert problem["type"].endswith("internal")


def test_error_event_leaks_nothing_from_the_exception() -> None:
    response = TestClient(_app([], fail=True)).get("/stream")

    assert "hunter2" not in response.text
    assert "db-internal" not in response.text
    assert "RuntimeError" not in response.text


def test_clean_stream_passes_through_unchanged() -> None:
    frames = ['event: segment\nid: 1\ndata: {"text":"a"}\n\n', "event: done\nid: 2\ndata: {}\n\n"]
    response = TestClient(_app(frames, fail=False)).get("/stream")

    assert response.text == "".join(frames)
