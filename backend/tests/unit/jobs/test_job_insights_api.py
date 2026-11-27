"""`GET /api/dashboard/job-insights`: nulls (not omitted) on empty data, per-user numbers, and
score bands that follow the caller's thresholds."""

from __future__ import annotations

import copy
from datetime import timedelta

import httpx

from app.adapters.fakes.system import FakeClock
from tests.conftest import MAINTAINER, USER, auth

ME = auth(MAINTAINER)
THEM = auth(USER)
PATH = "/api/dashboard/job-insights"


async def test_empty_insights_are_nulls(client: httpx.AsyncClient) -> None:
    response = await client.get(PATH, headers=ME)
    assert response.status_code == 200, response.text
    body = response.json()
    for key in (
        "response_rate",
        "interview_rate",
        "offer_rate",
        "ghost_rate",
        "median_days_to_response",
        "followup_on_time_rate",
    ):
        assert key in body
        assert body[key] is None, key
    assert body["applications_per_week"] == []
    assert [b["key"] for b in body["score_bands"]] == ["0-44", "45-54", "55-74", "75-100"]
    assert (await client.get(PATH)).status_code == 401
    assert (await client.get(f"{PATH}?from=2026-12-01&to=2026-11-01", headers=ME)).status_code in (
        400,
        422,
    )


async def test_insights_count_the_callers_applications(
    client: httpx.AsyncClient, clock: FakeClock
) -> None:
    created = await client.post(
        "/api/roles", json={"title": "Analyst", "status": "applied"}, headers=ME
    )
    assert created.status_code == 201
    role = created.json()
    clock.advance(timedelta(days=3))
    moved = await client.post(
        f"/api/roles/{role['id']}/status",
        json={"status": "interview", "version": role["version"]},
        headers=ME,
    )
    assert moved.status_code == 200, moved.text
    mine = (await client.get(PATH, headers=ME)).json()
    assert sum(w["applied"] for w in mine["applications_per_week"]) == 1
    assert mine["response_rate"] == 1
    assert mine["interview_rate"] == 1
    assert mine["ghost_rate"] == 0
    assert mine["median_days_to_response"] == 3
    assert mine["followup_on_time_rate"] == 0
    assert mine["by_source"] == [
        {"key": "manual", "found": 1, "applied": 1, "interviews": 1, "rejected": 0}
    ]
    theirs = (await client.get(PATH, headers=THEM)).json()
    assert theirs["response_rate"] is None

    settings = copy.deepcopy((await client.get("/api/settings", headers=ME)).json())
    settings.pop("updated_at")
    settings["scoring"] = {
        "keep_score": 60,
        "borderline_score": 50,
        "notify_score": 85,
        "notify_mission_score": 70,
    }
    assert (await client.put("/api/settings", json=settings, headers=ME)).status_code == 200
    bands = [b["key"] for b in (await client.get(PATH, headers=ME)).json()["score_bands"]]
    assert bands == ["0-49", "50-59", "60-84", "85-100", "unscored"]
