"""Human review routes: the latest review, the branch diff and the patch (redacted, 404s, the
diff cap), and the card's branch fields and review moves (spec getTaskReview, getTaskDiff,
getTaskPatch, Task)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import httpx
import pytest

from app.adapters.fakes.devboard_reviews import FakeBranchViews
from app.compose.devboard import DevboardRuntime
from app.domain.devboard.model import (
    Column,
    ReviewFinding,
    ReviewRecord,
    ReviewSeverity,
    ReviewVerdict,
)
from app.domain.devboard.reviews import MAX_DIFF_CHARS, ReviewService
from app.domain.ports.devboard import BranchDiff
from app.main import Container, build_app
from tests.conftest import MAINTAINER, USER, auth

AS_MAINTAINER, AS_USER = auth(MAINTAINER), auth(USER)
SECRET = "sk-test-0000"
BRANCH = "agent/0000aaaa-1111bbbb"
BASE = "b" * 40
SCOPE = uuid.UUID(int=7)
AT = datetime(2026, 11, 28, tzinfo=UTC)


class MarkRedactor:
    async def redact(self, text: str) -> str:
        return text.replace(SECRET, "[redacted]")


@dataclass
class Ui:
    http: httpx.AsyncClient
    runtime: DevboardRuntime
    branches: FakeBranchViews

    async def card(self, *, branch: str | None = BRANCH, column: Column | None = None) -> str:
        response = await self.http.post("/api/tasks", json={"title": "t"}, headers=AS_MAINTAINER)
        task_id = uuid.UUID(response.json()["id"])
        await self.runtime.dispatcher.drain()
        tasks = self.runtime.stores.tasks
        current = await tasks.get(SCOPE, task_id)
        assert current is not None
        changed = replace(current, branch=branch, base_sha=BASE if branch else None, review_round=1)
        await tasks.save(SCOPE, replace(changed, column=column or current.column), current.version)
        return str(task_id)


@pytest.fixture
async def ui(container: Container) -> AsyncGenerator[Ui]:
    app = build_app(container)
    runtime = app.state.devboard_runtime
    assert isinstance(runtime, DevboardRuntime)
    branches = FakeBranchViews()
    reviews = ReviewService(runtime.stores.tasks, branches, MarkRedactor())
    app.state.devboard = replace(runtime.services, reviews=reviews)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as http:
        yield Ui(http, runtime, branches)


def _review(task_id: str) -> ReviewRecord:
    blocker = ReviewFinding(
        ReviewSeverity.BLOCKER,
        path="backend/src/app/api/x.py",
        evidence=f"Leaks {SECRET}.",
        rubric_id="SEC-06",
        line=3,
        fix="Redact it.",
    )
    nit = ReviewFinding(ReviewSeverity.NIT, path="x.py", evidence="Naming.", downgraded=True)
    return ReviewRecord(
        id=uuid.uuid4(),
        task_id=uuid.UUID(task_id),
        run_id=uuid.uuid4(),
        round=1,
        verdict=ReviewVerdict.CHANGES_REQUESTED,
        summary=f"Found {SECRET}.",
        findings=(blocker, nit),
        created_at=AT,
        model="claude-opus",
    )


async def test_the_latest_review_is_served_redacted(ui: Ui) -> None:
    task_id = await ui.card()
    await ui.runtime.stores.tasks.add_review(SCOPE, _review(task_id))
    response = await ui.http.get(f"/api/tasks/{task_id}/review", headers=AS_USER)
    assert response.status_code == 200, response.text
    body = response.json()
    assert SECRET not in response.text
    assert body["verdict"] == "changes_requested"
    assert body["summary"] == "Found [redacted]."
    blocker, nit = body["findings"]
    assert blocker["severity"] == "BLOCKER"
    assert blocker["rubric_id"] == "SEC-06"
    assert blocker["evidence"] == "Leaks [redacted]."
    assert nit["downgraded"] is True
    assert "downgraded" not in blocker


@pytest.mark.parametrize("what", ["review", "diff", "patch"])
async def test_reads_are_404_for_an_unknown_card(ui: Ui, what: str) -> None:
    response = await ui.http.get(f"/api/tasks/{uuid.uuid4()}/{what}", headers=AS_USER)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_review_is_404_before_any_review(ui: Ui) -> None:
    task_id = await ui.card()
    response = await ui.http.get(f"/api/tasks/{task_id}/review", headers=AS_USER)
    assert response.status_code == 404


@pytest.mark.parametrize("what", ["diff", "patch"])
async def test_diff_and_patch_are_404_without_a_branch(ui: Ui, what: str) -> None:
    task_id = await ui.card(branch=None)
    response = await ui.http.get(f"/api/tasks/{task_id}/{what}", headers=AS_USER)
    assert response.status_code == 404


async def test_the_diff_is_redacted_with_its_stat(ui: Ui) -> None:
    task_id = await ui.card()
    ui.branches.publish(BranchDiff(BRANCH, BASE, 2, 5, 1, f"+token = '{SECRET}'\n"))
    response = await ui.http.get(f"/api/tasks/{task_id}/diff", headers=AS_USER)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "branch": BRANCH,
        "base_sha": BASE,
        "files_changed": 2,
        "insertions": 5,
        "deletions": 1,
        "diff": "+token = '[redacted]'\n",
        "truncated": False,
    }


async def test_a_long_diff_is_cut_and_marked_truncated(ui: Ui) -> None:
    task_id = await ui.card()
    ui.branches.publish(BranchDiff(BRANCH, BASE, 1, 1, 0, "+" * (MAX_DIFF_CHARS + 10)))
    body = (await ui.http.get(f"/api/tasks/{task_id}/diff", headers=AS_USER)).json()
    assert body["truncated"] is True
    assert len(body["diff"]) == MAX_DIFF_CHARS


async def test_the_patch_downloads_redacted(ui: Ui) -> None:
    task_id = await ui.card()
    patch = f"From 0000 Mon Sep 17 00:00:00 2001\n+key={SECRET}\n"
    ui.branches.publish(BranchDiff(BRANCH, BASE, 1, 1, 0, ""), patch)
    response = await ui.http.get(f"/api/tasks/{task_id}/patch", headers=AS_USER)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/x-patch")
    assert "attachment" in response.headers["content-disposition"]
    assert response.text == "From 0000 Mon Sep 17 00:00:00 2001\n+key=[redacted]\n"


@pytest.mark.parametrize("what", ["review", "diff", "patch"])
async def test_review_reads_need_a_signed_in_user(ui: Ui, what: str) -> None:
    task_id = await ui.card()
    assert (await ui.http.get(f"/api/tasks/{task_id}/{what}")).status_code == 401


async def test_a_card_shows_its_branch_and_review_round(ui: Ui) -> None:
    task_id = await ui.card()
    body = (await ui.http.get(f"/api/tasks/{task_id}", headers=AS_USER)).json()
    assert (body["branch"], body["base_sha"], body["review_round"]) == (BRANCH, BASE, 1)


async def test_only_a_maintainer_may_end_human_review(ui: Ui) -> None:
    task_id = await ui.card(column=Column.HUMAN_REVIEW)
    as_maintainer = (await ui.http.get(f"/api/tasks/{task_id}", headers=AS_MAINTAINER)).json()
    as_user = (await ui.http.get(f"/api/tasks/{task_id}", headers=AS_USER)).json()
    assert {"done", "changes_requested"} <= set(as_maintainer["allowed_columns"])
    assert not {"done", "changes_requested"} & set(as_user.get("allowed_columns", []))
    move = {"to_column": "done", "rank": "U", "version": as_user["version"]}
    response = await ui.http.post(f"/api/tasks/{task_id}/move", json=move, headers=AS_USER)
    assert response.status_code == 403


async def test_request_changes_without_notes_is_refused(ui: Ui) -> None:
    task_id = await ui.card(column=Column.HUMAN_REVIEW)
    task = (await ui.http.get(f"/api/tasks/{task_id}", headers=AS_MAINTAINER)).json()
    move = {"to_column": "changes_requested", "rank": "U", "version": task["version"]}
    response = await ui.http.post(f"/api/tasks/{task_id}/move", json=move, headers=AS_MAINTAINER)
    assert response.status_code == 422


async def test_the_default_server_has_no_diff_for_a_new_card(client: httpx.AsyncClient) -> None:
    created = await client.post("/api/tasks", json={"title": "t"}, headers=AS_MAINTAINER)
    response = await client.get(f"/api/tasks/{created.json()['id']}/diff", headers=AS_USER)
    assert response.status_code == 404
