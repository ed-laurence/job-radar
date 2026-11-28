"""The coordinator's HTTP contract for review runs (docs/features/agent-review.md §2) and rework
dev runs (§3): the isolated review page is redacted and under the result cap, a review token
can't claim leases (403 problem, nothing stored) or reach another task, and a rework run's page
carries `review_blockers`."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx
from tests.unit.coordinator.support import BASE_URL, OTHER_OWNER, OWNER, Harness, call, harness

from app.coordinator.schemas import RESULT_TOKENS
from app.coordinator.server import build_app
from app.domain.devboard.messages import estimate_tokens
from app.domain.devboard.model import (
    ReviewFinding,
    ReviewRecord,
    ReviewSeverity,
    ReviewVerdict,
    RunKind,
    TaskRecord,
)

BRANCH = "agent/aaaaaaaa-bbbbbbbb"
BASE = "e" * 40
SECRET_LIKE = "AKIAABCDEFGHIJKLMNOP"


async def _card(h: Harness, *, owner: uuid.UUID = OWNER) -> TaskRecord:
    task = await h.add_task(owner=owner, body=f"Brief with key {SECRET_LIKE} inside.")
    published = replace(task, branch=BRANCH, base_sha=BASE, review_round=1)
    return await h.tasks.save(owner, published, task.version)


@asynccontextmanager
async def client(h: Harness) -> AsyncGenerator[httpx.AsyncClient]:
    app = build_app(h.runtime)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as http,
    ):
        yield http


def _ok(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert estimate_tokens(json.dumps(body)) <= RESULT_TOKENS
    return body


async def test_the_review_page_is_redacted_and_under_the_cap() -> None:
    h = harness()
    task = await _card(h)
    run = await h.add_run(task, agent="reviewer", kind=RunKind.REVIEW)
    async with client(h) as http:
        body = _ok(await call(http, h.token(task.id, run.id), "GET", "/v1/task"))
    assert SECRET_LIKE not in json.dumps(body)
    assert body["review"]["branch"] == BRANCH
    assert "subtasks" not in body
    assert "leases_held" not in body


async def test_a_review_token_can_not_claim_and_nothing_is_stored() -> None:
    h = harness()
    task = await _card(h)
    run = await h.add_run(task, agent="reviewer", kind=RunKind.REVIEW)
    async with client(h) as http:
        response = await call(
            http, h.token(task.id, run.id), "POST", "/v1/leases/claim", {"paths": ["web/src/"]}
        )
    assert response.status_code == 403, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert h.leases.leases == {}


async def test_a_review_token_for_another_tasks_run_is_403() -> None:
    h = harness()
    mine = await _card(h)
    theirs = await _card(h, owner=OTHER_OWNER)
    their_run = await h.add_run(theirs, agent="reviewer", kind=RunKind.REVIEW)
    async with client(h) as http:
        response = await call(http, h.token(mine.id, their_run.id), "GET", "/v1/task")
    assert response.status_code == 403, response.text
    assert str(theirs.id) not in response.text


async def test_a_rework_page_carries_the_blockers_redacted() -> None:
    h = harness()
    task = await _card(h)
    blocker = ReviewFinding(
        ReviewSeverity.BLOCKER,
        "web/src/features/contacts/ContactList.tsx",
        f"Hard-coded key {SECRET_LIKE}.",
        rubric_id="ARCH-43",
        fix="Read it from config.",
    )
    review = ReviewRecord(
        id=uuid.uuid4(),
        task_id=task.id,
        run_id=uuid.uuid4(),
        round=1,
        verdict=ReviewVerdict.CHANGES_REQUESTED,
        summary="One blocker.",
        findings=(blocker,),
        created_at=datetime(2026, 1, 5, tzinfo=UTC),
    )
    await h.tasks.add_review(OWNER, review)
    run = await h.add_run(task, agent="writer")
    async with client(h) as http:
        body = _ok(await call(http, h.token(task.id, run.id), "GET", "/v1/task"))
    (shown,) = body["review_blockers"]
    assert shown["citation"] == "ARCH-43"
    assert SECRET_LIKE not in json.dumps(body)
