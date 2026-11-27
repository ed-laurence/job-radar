"""Maintainer health panel (spec tag Health)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.security import maintainer, rate_limited_user
from app.domain.health import HealthService, HealthView
from app.domain.identity import Principal
from app.domain.rate_limits import RateTier
from app.generated.models import (
    ByFeatureItem,
    HealthSummary,
    Job,
    Llm,
    Site,
    WebVitalsP75Item,
)

router = APIRouter(prefix="/api/health", tags=["Health"])

Maintainer = Annotated[Principal, Depends(rate_limited_user(RateTier.DEFAULT, maintainer))]


def _health(request: Request) -> HealthService:
    found = request.app.state.health
    assert isinstance(found, HealthService)
    return found


@router.get("/summary", operation_id="getHealthSummary", response_model=HealthSummary)
async def get_health_summary(request: Request, principal: Maintainer) -> HealthSummary:
    return _summary(await _health(request).summary(principal))


def _summary(view: HealthView) -> HealthSummary:
    llm = view.llm
    return HealthSummary(
        jobs=[Job.model_validate(job, from_attributes=True) for job in view.jobs],
        llm=Llm(
            spend_today_aud=llm.spend_today_aud,
            spend_month_aud=llm.spend_month_aud,
            budget_month_aud=llm.budget_month_aud,
            calls_today=llm.calls_today,
            fallback_rate_7d=llm.fallback_rate_7d,
            by_feature=[
                ByFeatureItem.model_validate(row, from_attributes=True) for row in llm.by_feature
            ],
        ),
        site=Site(
            page_views_7d=view.site.page_views_7d,
            sessions_7d=view.site.sessions_7d,
            web_vitals_p75=[
                WebVitalsP75Item.model_validate(v, from_attributes=True)
                for v in view.site.web_vitals_p75
            ],
        ),
        as_of=view.as_of,
    )
