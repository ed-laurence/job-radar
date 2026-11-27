"""Dashboard KPIs and chart series (spec tag Dashboard)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.context import services
from app.api.jobs import mapping
from app.api.jobs.deps import DefaultUser, jobs
from app.domain.jobs.dashboard import DashboardService
from app.domain.jobs.metrics import DateRange, resolve_range
from app.generated.models import (
    AgentPassRateSeries,
    CycleTimeByColumnItem,
    CycleTimeSeries,
    DashboardKpis,
    DevMetrics,
    FunnelSeries,
    Item,
    Item1,
    Item2,
    Item3,
    Item4,
    Item5,
    Item6,
    JobInsights,
    JobSummary,
    LlmSpendSeries,
    PostToApplySeries,
    RejectionReasonSeries,
    RunOutcome,
    ScoreDistribution,
    StatusTabCounts,
    ThroughputItem,
    WipByColumnItem,
)

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


def _window(
    request: Request,
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
    bucket: Annotated[str, Query(pattern=r"^(day|week|month)$", max_length=5)] = "week",
) -> DateRange:
    return resolve_range(start, end, bucket, services(request).clock.now().date())


Window = Annotated[DateRange, Depends(_window)]


def _range(
    request: Request,
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
) -> DateRange:
    return resolve_range(start, end, "week", services(request).clock.now().date())


Range = Annotated[DateRange, Depends(_range)]


def _dashboard(request: Request) -> DashboardService:
    return jobs(request).dashboard


@router.get(
    "/kpis",
    operation_id="getDashboardKpis",
    response_model=DashboardKpis,
    response_model_exclude_none=True,
)
async def get_kpis(request: Request, principal: DefaultUser, window: Window) -> DashboardKpis:
    return mapping.kpis(await _dashboard(request).kpis(principal, window))


@router.get("/funnel", operation_id="getFunnel", response_model=FunnelSeries)
async def get_funnel(request: Request, principal: DefaultUser, window: Window) -> FunnelSeries:
    rows = await _dashboard(request).funnel(principal, window)
    return FunnelSeries(
        items=[Item.model_validate({"status": r.key, "count": r.count}) for r in rows]
    )


@router.get(
    "/post-to-apply",
    operation_id="getPostToApply",
    response_model=PostToApplySeries,
    response_model_exclude_none=True,
)
async def get_post_to_apply(
    request: Request, principal: DefaultUser, window: Window
) -> PostToApplySeries:
    view = await _dashboard(request).post_to_apply(principal, window)
    return PostToApplySeries(
        median_hours=view.median_hours,
        p75_hours=view.p75_hours,
        items=[Item1.model_validate(item, from_attributes=True) for item in view.items],
    )


@router.get(
    "/score-distribution", operation_id="getScoreDistribution", response_model=ScoreDistribution
)
async def get_score_distribution(
    request: Request, principal: DefaultUser, window: Window
) -> ScoreDistribution:
    bins = await _dashboard(request).score_distribution(principal, window)
    return ScoreDistribution(items=[Item2(min=b.low, max=b.high, count=b.count) for b in bins])


@router.get(
    "/rejection-reasons", operation_id="getRejectionReasons", response_model=RejectionReasonSeries
)
async def get_rejection_reasons(
    request: Request, principal: DefaultUser, window: Window
) -> RejectionReasonSeries:
    rows = await _dashboard(request).rejection_reasons(principal, window)
    items = [Item3.model_validate({"category": r.key, "count": r.count}) for r in rows]
    return RejectionReasonSeries(items=items)


@router.get(
    "/llm-spend",
    operation_id="getLlmSpend",
    response_model=LlmSpendSeries,
    response_model_exclude_none=True,
)
async def get_llm_spend(request: Request, principal: DefaultUser, window: Window) -> LlmSpendSeries:
    rows = await _dashboard(request).llm_spend(principal, window)
    return LlmSpendSeries(items=[Item4.model_validate(row, from_attributes=True) for row in rows])


@router.get(
    "/dev-cycle-time",
    operation_id="getDevCycleTime",
    response_model=CycleTimeSeries,
    response_model_exclude_none=True,
)
async def get_dev_cycle_time(
    request: Request, principal: DefaultUser, window: Window
) -> CycleTimeSeries:
    rows = await _dashboard(request).cycle_time(principal, window)
    return CycleTimeSeries(items=[Item5.model_validate(row, from_attributes=True) for row in rows])


@router.get(
    "/agent-pass-rate",
    operation_id="getAgentPassRate",
    response_model=AgentPassRateSeries,
    response_model_exclude_none=True,
)
async def get_agent_pass_rate(
    request: Request, principal: DefaultUser, window: Window
) -> AgentPassRateSeries:
    rows = await _dashboard(request).agent_pass_rate(principal, window)
    return AgentPassRateSeries(items=[Item6.model_validate(r, from_attributes=True) for r in rows])


@router.get("/job-summary", operation_id="getJobSummary", response_model=JobSummary)
async def get_job_summary(request: Request, principal: DefaultUser) -> JobSummary:
    view = await _dashboard(request).job_summary(principal)
    return JobSummary(
        to_act_on=view.to_act_on,
        oldest_waiting_hours=view.oldest_waiting_hours,
        applied_last_7_days=view.applied_last_7_days,
        median_post_to_apply_hours=view.median_post_to_apply_hours,
        followups_due=view.followups_due,
        tab_counts=StatusTabCounts.model_validate(view.tab_counts),
        as_of=view.as_of,
    )


@router.get("/dev-metrics", operation_id="getDevMetrics", response_model=DevMetrics)
async def get_dev_metrics(request: Request, principal: DefaultUser, window: Range) -> DevMetrics:
    view = await _dashboard(request).dev_metrics(principal, window)
    return DevMetrics.model_validate(
        {
            "throughput": [ThroughputItem(week_start=w, done=n) for w, n in view.throughput],
            "wip_by_column": [
                WipByColumnItem.model_validate({"column": c, "count": n})
                for c, n in view.wip_by_column
            ],
            "cycle_time_by_column": [
                CycleTimeByColumnItem.model_validate({"column": c, "hours_p50": h})
                for c, h in view.cycle_time_by_column
            ],
            "lead_time_hours_p50": view.lead_time_hours_p50,
            "lead_time_hours_p90": view.lead_time_hours_p90,
            "agent_pass_rate": view.agent_pass_rate,
            "avg_review_rounds": view.avg_review_rounds,
            "out_of_scope_rate": view.out_of_scope_rate,
            "avg_cost_per_task_usd": view.avg_cost_per_task_usd,
            "run_outcomes": [
                RunOutcome.model_validate({"outcome": o, "count": n}) for o, n in view.run_outcomes
            ],
            "blocked_count": view.blocked_count,
            "oldest_human_review_hours": view.oldest_human_review_hours,
            "as_of": view.as_of,
        }
    )


@router.get("/job-insights", operation_id="getJobInsights", response_model=JobInsights)
async def get_job_insights(request: Request, principal: DefaultUser, window: Range) -> JobInsights:
    return mapping.insights(await _dashboard(request).insights(principal, window))
