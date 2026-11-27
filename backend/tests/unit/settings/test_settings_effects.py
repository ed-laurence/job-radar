"""Saved settings take effect where their concepts live: follow-up timing (job summary, alerts),
the campaign start override, and the LLM budget on the health panel and in
the spend cap. The real app with in-memory stores."""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timedelta

import httpx
import pytest
from pydantic import BaseModel

from app.adapters.fakes.system import FakeClock
from app.adapters.llm.providers.replay import ReplayFixtureMissing
from app.domain.errors import BudgetExceeded
from app.domain.identity import derive_user_id
from app.domain.ports import LlmAttribution, LlmCallRecord, LlmFeature, LlmRequest
from app.main import Container
from tests.conftest import MAINTAINER, USER, auth

ME = auth(MAINTAINER)
THEM = auth(USER)


async def _save(client: httpx.AsyncClient, headers: dict[str, str], **changes: object) -> None:
    body = copy.deepcopy((await client.get("/api/settings", headers=headers)).json())
    body.pop("updated_at")
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(body.get(key), dict):
            body[key] = {**body[key], **value}
        else:
            body[key] = value
    response = await client.put("/api/settings", json=body, headers=headers)
    assert response.status_code == 200, response.text


async def _applied_role(client: httpx.AsyncClient, headers: dict[str, str]) -> None:
    body = {"title": "Data Engineer", "status": "applied"}
    assert (await client.post("/api/roles", json=body, headers=headers)).status_code == 201


async def test_followup_timing_drives_the_summary_and_alerts(
    client: httpx.AsyncClient, clock: FakeClock
) -> None:
    await _applied_role(client, ME)
    clock.advance(timedelta(hours=30))

    summary = (await client.get("/api/dashboard/job-summary", headers=ME)).json()
    assert summary["followups_due"] == 1
    titles = [a["title"] for a in (await client.get("/api/alerts", headers=ME)).json()["items"]]
    assert "1 application to follow up" in titles

    await _save(client, ME, followups={"after_hours": 36, "due_hours": 48})
    summary = (await client.get("/api/dashboard/job-summary", headers=ME)).json()
    assert summary["followups_due"] == 0
    alerts = (await client.get("/api/alerts", headers=ME)).json()["items"]
    assert all("follow up" not in a["title"] for a in alerts)

    clock.advance(timedelta(hours=7))
    alerts = (await client.get("/api/alerts", headers=ME)).json()["items"]
    due = [a for a in alerts if a["title"] == "1 application to follow up"]
    assert len(due) == 1
    assert "more than 36 hours ago" in due[0]["text"]


async def test_the_campaign_start_override(client: httpx.AsyncClient) -> None:
    before = (await client.get("/api/campaign", headers=ME)).json()
    await _save(client, ME, campaign={"start_date": "2026-10-07"})
    plan = (await client.get("/api/campaign", headers=ME)).json()
    # Week 1 starts on the Monday of the chosen date.
    assert plan["start"] == "2026-10-05"
    assert plan["start"] != before["start"]
    await _save(client, ME, campaign={"start_date": None})
    assert (await client.get("/api/campaign", headers=ME)).json()["start"] == before["start"]


async def test_the_llm_budget_shows_on_health_and_caps_spend(
    client: httpx.AsyncClient, container: Container, clock: FakeClock
) -> None:
    health = (await client.get("/api/health/summary", headers=ME)).json()
    assert health["llm"].get("budget_month_aud") is None
    await _save(client, ME, llm={"monthly_budget_aud": 2.0})
    health = (await client.get("/api/health/summary", headers=ME)).json()
    assert health["llm"]["budget_month_aud"] == 2.0

    me = derive_user_id(MAINTAINER)
    await container.usage.record_call(_call(me, clock.now(), 2.5))
    request = LlmRequest(
        feature=LlmFeature.SCORE,
        system="Score.",
        instructions="Score it.",
        attribution=LlmAttribution(user_id=me),
    )
    with pytest.raises(BudgetExceeded, match="month"):
        await container.llm.generate(request, _Answer)
    # Another user's spend is their own: their call gets past the cap to the provider.
    other = LlmRequest(
        feature=LlmFeature.SCORE,
        system="Score.",
        instructions="Score it.",
        attribution=LlmAttribution(user_id=derive_user_id(USER)),
    )
    with pytest.raises(ReplayFixtureMissing):
        await container.llm.generate(other, _Answer)


class _Answer(BaseModel):
    score: int


def _call(user_id: uuid.UUID, at: datetime, cost: float) -> LlmCallRecord:
    return LlmCallRecord(
        user_id=user_id,
        at=at,
        feature=LlmFeature.SCORE,
        provider="replay",
        model="m",
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
        cost_usd=cost,
        latency_ms=1,
        is_ok=True,
        is_fallback=False,
        guard_input="pass",
        guard_output=None,
        error_code=None,
    )
