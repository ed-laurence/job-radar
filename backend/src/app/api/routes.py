"""Foundation routes: health, the caller, and first-party analytics.

Everything under `/api` matches `api/openapi.yaml` (`servers: /api`); `/healthz` is at the root.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import Field

from app.api.context import services
from app.api.security import maintainer, rate_limited_public, rate_limited_user
from app.domain.analytics import AnalyticsSummaryView, WebEventInput
from app.domain.identity import Principal
from app.domain.rate_limits import RateTier
from app.generated.models import (
    AnalyticsSummary,
    Funnel,
    Health,
    Me,
    PageViewEvent,
    ProductEvent,
    Step1,
    ViewsByPageItem,
    WebEventAck,
    WebEventBatch,
    WebVital,
    WebVitalEvent,
)
from app.generated.models import AppRole as ApiRole


class MeBody(Me):
    """`Me` with a plain-string email. The generated `EmailStr` rejects reserved TLDs such as the
    seed accounts' `example.test`; allowlist entries are already validated as plain ASCII."""

    email: str = Field(..., max_length=254)  # pyright: ignore[reportIncompatibleVariableOverride]


root = APIRouter()
api = APIRouter(prefix="/api")

DefaultUser = Annotated[Principal, Depends(rate_limited_user(RateTier.DEFAULT))]
EventsUser = Annotated[Principal, Depends(rate_limited_user(RateTier.EVENTS))]
Maintainer = Annotated[Principal, Depends(rate_limited_user(RateTier.DEFAULT, maintainer))]


@root.get(
    "/healthz",
    operation_id="getHealth",
    response_model=Health,
    dependencies=[Depends(rate_limited_public)],
)
async def get_health() -> Health:
    return Health(status="ok")


@api.get("/me", operation_id="getMe", response_model=MeBody)
async def get_me(principal: DefaultUser) -> MeBody:
    return MeBody(
        id=principal.user_id,
        email=principal.email,
        role=ApiRole(principal.role.value),
        display_name=principal.display_name,
    )


@api.post(
    "/events",
    operation_id="ingestWebEvents",
    response_model=WebEventAck,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_web_events(
    batch: WebEventBatch, request: Request, principal: EventsUser
) -> WebEventAck:
    inputs = [_event_input(event.root) for event in batch.events]
    accepted = await services(request).analytics.ingest(inputs, principal.role)
    return WebEventAck(accepted=accepted)


@api.get("/analytics/summary", operation_id="getAnalyticsSummary", response_model=AnalyticsSummary)
async def get_analytics_summary(
    request: Request,
    _principal: Maintainer,
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
) -> AnalyticsSummary:
    return _summary(await services(request).analytics.summary(start, end))


def _event_input(event: PageViewEvent | ProductEvent | WebVitalEvent) -> WebEventInput:
    if isinstance(event, PageViewEvent):
        return WebEventInput(
            type="page_view", route=event.route, at=event.at, session_id=event.session_id
        )
    if isinstance(event, ProductEvent):
        return WebEventInput(
            type="product",
            route=event.route,
            at=event.at,
            session_id=event.session_id,
            name=event.name.value,
        )
    assert isinstance(event, WebVitalEvent)
    return WebEventInput(
        type="web_vital",
        route=event.route,
        at=event.at,
        session_id=event.session_id,
        metric=event.metric.value,
        value=event.value,
        rating=event.rating.value if event.rating else None,
    )


def _summary(view: AnalyticsSummaryView) -> AnalyticsSummary:
    return AnalyticsSummary(
        views_by_page=[
            ViewsByPageItem(route=v.route, views=v.views, sessions=v.sessions)
            for v in view.views_by_page
        ],
        funnels=[
            Funnel(name=f.name, steps=[Step1(name=s.name, sessions=s.sessions) for s in f.steps])
            for f in view.funnels
        ],
        web_vitals=[
            WebVital.model_validate(
                {
                    "route": v.route,
                    "metric": v.metric,
                    "p75": v.p75,
                    "samples": v.samples,
                    "budget": v.budget,
                    "over_budget": v.is_over_budget,
                }
            )
            for v in view.web_vitals
        ],
    )
