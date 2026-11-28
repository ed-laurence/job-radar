"""getJobSummary, getDevMetrics and getHealthSummary: routed as the spec says, maintainer-only
where declared, and their bodies validate against the generated spec models (empty and seeded)."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import timedelta
from typing import cast

import httpx
import pytest
import yaml
from fastapi import FastAPI
from tests.conftest import MAINTAINER, USER, auth

from app.adapters.fakes.health import MemoryHealth
from app.adapters.fakes.jobs import MemoryRoles
from app.adapters.fakes.jobs_dashboard import MemoryDashboard
from app.adapters.fakes.system import FakeClock
from app.compose.jobs import JobsRuntime
from app.domain.jobs.ports import ColumnEntry, DevBoardFacts, RunFact, TaskFact
from app.domain.ports import LlmCallRecord, LlmFeature
from app.domain.ports.health import JobRunFact
from app.generated.models import DevMetrics, HealthSummary, JobSummary
from app.main import Container, build_app
from app.settings import Settings

ME = auth(MAINTAINER)
OPERATIONS = {
    "getJobSummary": ("get", "/api/dashboard/job-summary"),
    "getDevMetrics": ("get", "/api/dashboard/dev-metrics"),
    "getHealthSummary": ("get", "/api/health/summary"),
}


@pytest.fixture
def app(container: Container) -> FastAPI:
    return build_app(container)


@pytest.fixture
async def http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test")


def _runtime(app: FastAPI) -> JobsRuntime:
    runtime = app.state.jobs_runtime
    assert isinstance(runtime, JobsRuntime)
    return runtime


def test_operations_are_routed_as_specified(settings: Settings, app: FastAPI) -> None:
    spec = cast("dict[str, object]", yaml.safe_load(settings.openapi_path.read_text("utf-8")))
    paths = cast("dict[str, dict[str, dict[str, object]]]", spec["paths"])
    served = app.openapi()["paths"]
    for operation_id, (method, path) in OPERATIONS.items():
        declared = paths[path.removeprefix("/api")][method]
        assert declared["operationId"] == operation_id
        assert served[path][method]["operationId"] == operation_id
    assert paths["/health/summary"]["get"]["x-required-role"] == "maintainer"


async def test_job_summary_empty_is_zeros_and_nulls(http: httpx.AsyncClient) -> None:
    response = await http.get("/api/dashboard/job-summary", headers=ME)
    assert response.status_code == 200, response.text
    body = JobSummary.model_validate(response.json())
    assert body.to_act_on == 0
    assert body.oldest_waiting_hours is None
    assert body.median_post_to_apply_hours is None
    assert response.json()["oldest_waiting_hours"] is None


async def _role(http: httpx.AsyncClient, status: str) -> uuid.UUID:
    role = (await http.post("/api/roles", json={"title": "Data Engineer"}, headers=ME)).json()
    if status != "new":
        moved = await http.post(
            f"/api/roles/{role['id']}/status", json={"status": status, "version": 1}, headers=ME
        )
        assert moved.status_code == 200, moved.text
    return uuid.UUID(str(role["id"]))


async def test_job_summary_counts_tabs(app: FastAPI, http: httpx.AsyncClient) -> None:
    for status in ("new", "new", "reviewing", "applied", "interview", "offer", "closed"):
        await _role(http, status)
    filtered = await _role(http, "new")
    roles = _runtime(app).stores.roles
    assert isinstance(roles, MemoryRoles)
    owner = next(key for key in roles.roles if key[1] == filtered)
    roles.roles[owner] = dataclasses.replace(roles.roles[owner], stage="filtered")
    response = await http.get("/api/dashboard/job-summary", headers=ME)
    body = JobSummary.model_validate(response.json())
    assert body.to_act_on == 3
    assert body.oldest_waiting_hours is not None
    assert body.applied_last_7_days == 1
    tabs = body.tab_counts
    assert (tabs.act_now, tabs.applied, tabs.interviewing, tabs.passed) == (3, 1, 2, 1)
    assert (tabs.rejected, tabs.unknown, tabs.filtered) == (0, 0, 1)
    other = await http.get("/api/dashboard/job-summary", headers=auth(USER))
    assert JobSummary.model_validate(other.json()).to_act_on == 0  # per user


async def test_dev_metrics_empty_and_populated(
    app: FastAPI, http: httpx.AsyncClient, clock: FakeClock
) -> None:
    empty = await http.get("/api/dashboard/dev-metrics", headers=ME)
    assert empty.status_code == 200, empty.text
    body = DevMetrics.model_validate(empty.json())
    assert body.lead_time_hours_p50 is None
    assert body.agent_pass_rate is None
    assert "out_of_scope_rate" not in empty.json()
    assert {item.outcome.value for item in body.run_outcomes} == {
        "succeeded",
        "failed",
        "killed",
        "running",
        "queued",
    }
    dashboard = _runtime(app).stores.dashboard
    assert isinstance(dashboard, MemoryDashboard)
    task, now = uuid.uuid4(), clock.now()
    dashboard.dev_board_facts = DevBoardFacts(
        tasks=(TaskFact(id=task, column="done", created_at=now - timedelta(days=3)),),
        entries=(ColumnEntry(task_id=task, at=now - timedelta(days=1), column="done"),),
        runs=(
            RunFact(
                task_id=task,
                kind="review",
                status="succeeded",
                round=0,
                cost_usd=1.25,
                created_at=now - timedelta(days=2),
                outcome="approved",
                finished_at=now - timedelta(days=2),
            ),
        ),
    )
    response = await http.get("/api/dashboard/dev-metrics?from=2026-11-01", headers=ME)
    metrics = DevMetrics.model_validate(response.json())
    assert sum(item.done for item in metrics.throughput) == 1
    assert metrics.lead_time_hours_p50 == 48.0
    assert metrics.agent_pass_rate == 1.0
    assert metrics.avg_cost_per_task_usd == 1.25
    assert "out_of_scope_rate" not in response.json()


async def test_health_is_maintainer_only(http: httpx.AsyncClient) -> None:
    response = await http.get("/api/health/summary", headers=auth(USER))
    assert response.status_code == 403
    assert response.json()["type"].startswith("/problems/")
    assert (await http.get("/api/health/summary")).status_code == 401


async def test_health_empty_and_populated(
    app: FastAPI, http: httpx.AsyncClient, container: Container, clock: FakeClock
) -> None:
    empty = await http.get("/api/health/summary", headers=ME)
    assert empty.status_code == 200, empty.text
    body = HealthSummary.model_validate(empty.json())
    assert [job.name for job in body.jobs] == ["scan", "digest", "rollup", "watchdog"]
    assert {job.last_status.value for job in body.jobs} == {"never"}
    assert body.llm.budget_month_aud is None
    assert body.llm.fallback_rate_7d is None
    assert body.site.page_views_7d == 0

    source = app.state.health_source
    assert isinstance(source, MemoryHealth)
    now = clock.now()
    source.job_runs.append(JobRunFact(job="digest", started_at=now - timedelta(hours=2), ok=True))
    entry = app.state.services.allowlist.lookup(MAINTAINER)
    assert entry is not None
    await container.usage.record_call(
        LlmCallRecord(
            user_id=entry.user_id,
            at=now,
            feature=LlmFeature.SCORE,
            provider="vertex-claude",
            model="claude-haiku-5-5",
            input_tokens=10,
            output_tokens=5,
            cache_read_tokens=0,
            cost_usd=0.002,
            latency_ms=10,
            is_ok=True,
            is_fallback=True,
            guard_input="pass",
            guard_output=None,
        )
    )
    events = [
        {
            "type": "page_view",
            "route": "/",
            "at": now.isoformat(),
            "session_id": "session-000000001",
        },
        {
            "type": "web_vital",
            "route": "/",
            "at": now.isoformat(),
            "session_id": "session-000000001",
            "metric": "LCP",
            "value": 1800.0,
            "rating": "good",
        },
    ]
    sent = await http.post("/api/events", json={"events": events}, headers=ME)
    assert sent.status_code == 202, sent.text
    body = HealthSummary.model_validate((await http.get("/api/health/summary", headers=ME)).json())
    digest = next(job for job in body.jobs if job.name == "digest")
    assert digest.last_status.value == "ok"
    assert body.llm.calls_today == 1
    assert body.llm.fallback_rate_7d == 1.0
    assert body.llm.by_feature[0].feature == "score"
    assert (body.site.page_views_7d, body.site.sessions_7d) == (1, 1)
    lcp = body.site.web_vitals_p75[0]
    assert (lcp.metric.value, lcp.value, lcp.budget) == ("LCP", 1800.0, 2500.0)
