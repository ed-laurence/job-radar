"""Rejection analysis, rejection patterns and follow-up drafts: routed as the spec says, and
their JSON and SSE payloads validate against the generated spec models. Replay fixtures only."""

from __future__ import annotations

import json
import uuid
from typing import cast

import httpx
import pytest
import yaml
from fastapi import FastAPI
from tests.conftest import MAINTAINER, USER, auth

from app.compose.jobs import JobsRuntime
from app.generated.models import (
    DoneEvent,
    RejectionAnalysis,
    RejectionPatterns,
    SegmentEvent,
)
from app.main import Container, build_app
from app.settings import Settings

TAGS = {"Rejections", "Follow-ups"}
ME = auth(MAINTAINER)
AD = "Acme Pty Ltd needs a Senior Data Engineer. Questions to Sam Lee, Talent Partner."
REJECTION = "We went with a candidate with more hands-on Spark experience."


@pytest.fixture
def app(container: Container) -> FastAPI:
    return build_app(container)


@pytest.fixture
async def http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test")


def _sse(body: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], {**fields, "data": json.loads(fields["data"])}))
    return events


async def _role(
    http: httpx.AsyncClient, status: str, *, is_listed: bool = True
) -> dict[str, object]:
    body: dict[str, object] = {"title": "Senior Data Engineer"}
    if is_listed:
        body |= {"company": "Acme Pty Ltd", "description": AD}
    role = (await http.post("/api/roles", json=body, headers=ME)).json()
    moved = await http.post(
        f"/api/roles/{role['id']}/status", json={"status": status, "version": 1}, headers=ME
    )
    assert moved.status_code == 200, moved.text
    return role


def test_operations_are_routed_as_specified(settings: Settings, app: FastAPI) -> None:
    spec = cast("dict[str, object]", yaml.safe_load(settings.openapi_path.read_text("utf-8")))
    routed = cast("dict[str, dict[str, dict[str, object]]]", app.openapi()["paths"])
    found = 0
    for path, item in cast("dict[str, dict[str, object]]", spec["paths"]).items():
        for method, raw in item.items():
            operation = cast("dict[str, object]", raw)
            if method in {"parameters", "servers"}:
                continue
            if not set(cast("list[str]", operation.get("tags", []))) & TAGS:
                continue
            found += 1
            mine = routed["/api" + path][method]
            assert mine["operationId"] == operation["operationId"]
            success = {c for c in cast("dict[str, object]", operation["responses"]) if c[0] == "2"}
            assert success <= set(cast("dict[str, object]", mine["responses"]))
    assert found == 4


async def test_rejection_analysis_and_patterns(app: FastAPI, http: httpx.AsyncClient) -> None:
    role = await _role(http, "rejected", is_listed=False)
    path = f"/api/roles/{role['id']}/rejection-analysis"
    missing_text = await http.post(path, headers=ME)
    assert missing_text.status_code == 422
    assert missing_text.json()["type"] == "/problems/validation-failed"
    runtime = app.state.jobs_runtime
    assert isinstance(runtime, JobsRuntime)
    entry = app.state.services.allowlist.lookup(MAINTAINER)
    assert entry is not None
    await runtime.stores.roles.update_rejection(
        entry.user_id, uuid.UUID(str(role["id"])), {"rejection_text": REJECTION}
    )
    analysed = await http.post(path, headers=ME)
    assert analysed.status_code == 200, analysed.text
    body = RejectionAnalysis.model_validate(analysed.json())
    assert [category.value for category in body.reason_categories] == ["skills_gap"]
    patterns = await http.get("/api/rejections/patterns", headers=ME)
    assert patterns.status_code == 200
    summary = RejectionPatterns.model_validate(patterns.json())
    assert (summary.total, summary.analysed) == (1, 1)
    assert [(c.category.value, c.count) for c in summary.categories] == [("skills_gap", 1)]
    assert summary.lessons
    other = await http.get("/api/rejections/patterns", headers=auth(USER))
    assert RejectionPatterns.model_validate(other.json()).total == 0
    elsewhere = await http.post(path, headers=auth(USER))
    assert elsewhere.status_code == 404


async def test_followup_drafts_stream_typed_events(http: httpx.AsyncClient) -> None:
    role = await _role(http, "applied")
    response = await http.post(f"/api/roles/{role['id']}/followups/draft", headers=ME)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _sse(response.text)
    names = [name for name, _ in events]
    assert names[-1] == "done"
    assert set(names) <= {"segment", "retract", "done"}
    segments = [SegmentEvent.model_validate(raw) for name, raw in events if name == "segment"]
    assert segments
    assert {segment.data.part.value for segment in segments if segment.data.part} == {"followup"}
    text = "".join(segment.data.text for segment in segments)
    assert text.startswith("To: Sam Lee")
    done = DoneEvent.model_validate(events[-1][1])
    assert done.data.status == "1 drafts"
    assert done.data.result_id is not None


async def test_followups_need_an_application(http: httpx.AsyncClient) -> None:
    role = await _role(http, "reviewing")
    response = await http.post(f"/api/roles/{role['id']}/followups/draft", headers=ME)
    assert response.status_code == 422
    assert response.json()["type"] == "/problems/validation-failed"
    # Errors before the stream starts are plain problems, never an `error` event.
    assert not response.text.startswith("event:")
