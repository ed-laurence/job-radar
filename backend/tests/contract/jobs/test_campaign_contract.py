"""Campaign, alerts, archive, the Feedback and Outreach listings and the summary's new counts:
routed as the spec says, and their JSON validates against the generated spec models."""

from __future__ import annotations

from datetime import timedelta
from typing import cast

import httpx
import pytest
import yaml
from fastapi import FastAPI
from tests.conftest import MAINTAINER, USER, auth

from app.adapters.fakes.system import FakeClock
from app.generated.models import (
    AlertList,
    ArchiveResult,
    CampaignActivity,
    CampaignActivityList,
    CampaignPlan,
    CampaignTaskList,
    ContactPage,
    JobSummary,
    RejectionSummaryPage,
    Role,
)
from app.main import Container, build_app
from app.settings import Settings

TAGS = {"Campaign", "Alerts"}
NEW_OPERATIONS = {
    "getCampaign",
    "listCampaignTasks",
    "markCampaignTaskDone",
    "clearCampaignTaskDone",
    "listCampaignActivity",
    "logCampaignActivity",
    "deleteCampaignActivity",
    "listAlerts",
    "archiveOlderRoles",
    "listRejections",
}
ME = auth(MAINTAINER)


@pytest.fixture
def app(container: Container) -> FastAPI:
    return build_app(container)


@pytest.fixture
async def http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test")


def test_operations_are_routed_as_specified(settings: Settings, app: FastAPI) -> None:
    spec = cast("dict[str, object]", yaml.safe_load(settings.openapi_path.read_text("utf-8")))
    routed = cast("dict[str, dict[str, dict[str, object]]]", app.openapi()["paths"])
    found: set[str] = set()
    tagged = 0
    for path, item in cast("dict[str, dict[str, object]]", spec["paths"]).items():
        for method, raw in item.items():
            if method in {"parameters", "servers"}:
                continue
            operation = cast("dict[str, object]", raw)
            if set(cast("list[str]", operation.get("tags", []))) & TAGS:
                tagged += 1
            if operation["operationId"] not in NEW_OPERATIONS:
                continue
            found.add(str(operation["operationId"]))
            mine = routed["/api" + path][method]
            assert mine["operationId"] == operation["operationId"]
            success = {c for c in cast("dict[str, object]", operation["responses"]) if c[0] == "2"}
            assert success <= set(cast("dict[str, object]", mine["responses"]))
    assert found == NEW_OPERATIONS
    assert tagged == 8


async def test_plan_tasks_and_activity(http: httpx.AsyncClient) -> None:
    plan = await http.get("/api/campaign", headers=ME)
    assert plan.status_code == 200, plan.text
    body = CampaignPlan.model_validate(plan.json())
    assert body.total_weeks == 7
    assert body.week == min(max(body.current_week, 1), 7)
    assert [m.key for m in body.metrics][:2] == ["agency_calls", "submissions"]
    week2 = CampaignPlan.model_validate((await http.get("/api/campaign?week=2", headers=ME)).json())
    assert week2.week == 2
    assert week2.checkpoint is not None
    assert (await http.get("/api/campaign?week=0", headers=ME)).status_code == 422

    tasks = await http.get("/api/campaign/tasks?week=1", headers=ME)
    listed = CampaignTaskList.model_validate(tasks.json())
    first = listed.items[0]
    assert not first.done
    marked = await http.put(f"/api/campaign/tasks/{first.id}/done?week=1", headers=ME)
    assert marked.status_code == 204
    again = CampaignTaskList.model_validate(
        (await http.get("/api/campaign/tasks?week=1", headers=ME)).json()
    )
    assert again.items[0].done
    cleared = await http.delete(f"/api/campaign/tasks/{first.id}/done?week=1", headers=ME)
    assert cleared.status_code == 204
    missing = await http.put("/api/campaign/tasks/no-such-task/done?week=1", headers=ME)
    assert missing.status_code == 404
    assert (await http.put(f"/api/campaign/tasks/{first.id}/done", headers=ME)).status_code == 422

    today = FakeClock().now().date().isoformat()
    logged = await http.post(
        "/api/campaign/activity",
        json={"on_date": today, "kind": "agency_call", "who": "Sam Lee", "company": "Acme"},
        headers=ME,
    )
    assert logged.status_code == 201, logged.text
    activity = CampaignActivity.model_validate(logged.json())
    bad = await http.post(
        "/api/campaign/activity", json={"on_date": today, "kind": "bogus"}, headers=ME
    )
    assert bad.status_code == 422
    extra = await http.post(
        "/api/campaign/activity",
        json={"on_date": today, "kind": "screen", "user_id": "x"},
        headers=ME,
    )
    assert extra.status_code == 422
    week = CampaignActivityList.model_validate(
        (await http.get("/api/campaign/activity", headers=ME)).json()
    )
    assert [a.id for a in week.items] == [activity.id]
    now_plan = CampaignPlan.model_validate((await http.get("/api/campaign", headers=ME)).json())
    assert {m.key: m.actual for m in now_plan.metrics}["agency_calls"] == 1
    other = CampaignActivityList.model_validate(
        (await http.get("/api/campaign/activity", headers=auth(USER))).json()
    )
    assert other.items == []
    not_theirs = await http.delete(f"/api/campaign/activity/{activity.id}", headers=auth(USER))
    assert not_theirs.status_code == 404
    deleted = await http.delete(f"/api/campaign/activity/{activity.id}", headers=ME)
    assert deleted.status_code == 204


async def test_archive_older_alerts_feedback_outreach_and_summary(
    http: httpx.AsyncClient,
) -> None:
    posted = (FakeClock().now() - timedelta(days=40)).isoformat()
    old = (
        await http.post("/api/roles", json={"title": "Old role", "posted_at": posted}, headers=ME)
    ).json()
    archived = await http.post("/api/roles/archive-older", json={"days": 30}, headers=ME)
    assert archived.status_code == 200, archived.text
    assert ArchiveResult.model_validate(archived.json()).archived == 1
    after = Role.model_validate((await http.get(f"/api/roles/{old['id']}", headers=ME)).json())
    assert (after.status.value, after.version) == ("closed", old["version"] + 1)
    bad = await http.post("/api/roles/archive-older", json={"days": 0}, headers=ME)
    assert bad.status_code == 422

    role = (await http.post("/api/roles", json={"title": "Rejected role"}, headers=ME)).json()
    moved = await http.post(
        f"/api/roles/{role['id']}/status", json={"status": "rejected", "version": 1}, headers=ME
    )
    assert moved.status_code == 200, moved.text
    page = RejectionSummaryPage.model_validate(
        (await http.get("/api/rejections?reason_status=missing", headers=ME)).json()
    )
    assert [(i.title, i.reason_status.value) for i in page.items] == [("Rejected role", "missing")]
    analysed = await http.get("/api/rejections?reason_status=analysed", headers=ME)
    assert RejectionSummaryPage.model_validate(analysed.json()).items == []

    contact = await http.post(
        "/api/contacts",
        json={"first_name": "Alex", "url": "https://people.example.test/alex"},
        headers=ME,
    )
    assert contact.status_code == 201, contact.text
    outreach = ContactPage.model_validate(
        (await http.get("/api/contacts?needs_outreach=true", headers=ME)).json()
    )
    assert len(outreach.items) == 1

    summary = JobSummary.model_validate(
        (await http.get("/api/dashboard/job-summary", headers=ME)).json()
    )
    assert (summary.tab_counts.outreach, summary.tab_counts.feedback) == (1, 1)
    assert summary.followups_due == 0

    alerts = await http.get("/api/alerts", headers=ME)
    assert alerts.status_code == 200
    AlertList.model_validate(alerts.json())
