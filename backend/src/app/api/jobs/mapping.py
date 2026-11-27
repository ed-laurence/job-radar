"""Domain records to generated response models. Field names match, so mapping is by attribute;
the routes serialise with `exclude_none` because the spec never sends null."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict

from pydantic import ValidationError

from app.agents.research.service import RunEnded
from app.domain.jobs.dashboard import KpiView
from app.domain.jobs.evidence import role_evidence
from app.domain.jobs.insights import InsightsView
from app.domain.jobs.records import (
    ChatRecord,
    ContactRecord,
    Page,
    RejectionRecord,
    ResearchRunRecord,
    RoleEventRecord,
    RoleRecord,
    TargetRecord,
)
from app.domain.jobs.rejections import RejectionPatterns as RejectionPatternsView
from app.generated.models import (
    Category,
    ChatMessage,
    ChatMessagePage,
    Contact,
    ContactPage,
    DashboardKpis,
    JobInsights,
    Lesson,
    RejectionAnalysis,
    RejectionCategory,
    RejectionPatterns,
    ResearchRun,
    Role,
    RoleEvent,
    RoleEventPage,
    RoleEvidence,
    RolePage,
    Source1,
    Target,
    TargetPage,
)


def role(record: RoleRecord) -> Role:
    model = Role.model_validate(record, from_attributes=True)
    found = role_evidence(record.score, record.score_breakdown)
    if found is None:
        return model
    evidence = RoleEvidence.model_validate(found, from_attributes=True)
    if not found.items:
        evidence = evidence.model_copy(update={"items": None})
    return model.model_copy(update={"evidence": evidence})


def role_page(page: Page[RoleRecord]) -> RolePage:
    return RolePage(items=[role(item) for item in page.items], next_cursor=page.next_cursor)


def event_page(page: Page[RoleEventRecord]) -> RoleEventPage:
    items = [RoleEvent.model_validate(item, from_attributes=True) for item in page.items]
    return RoleEventPage(items=items, next_cursor=page.next_cursor)


def contact(record: ContactRecord) -> Contact:
    return Contact.model_validate(record, from_attributes=True)


def contact_page(page: Page[ContactRecord]) -> ContactPage:
    return ContactPage(items=[contact(item) for item in page.items], next_cursor=page.next_cursor)


def target(record: TargetRecord) -> Target:
    values = {**asdict(record), "sources": _urls(record.sources) or None}
    return Target.model_validate(values)


def target_page(page: Page[TargetRecord]) -> TargetPage:
    return TargetPage(items=[target(item) for item in page.items], next_cursor=page.next_cursor)


def chat_page(page: Page[ChatRecord]) -> ChatMessagePage:
    items = [ChatMessage.model_validate(item, from_attributes=True) for item in page.items]
    return ChatMessagePage(items=items, next_cursor=page.next_cursor)


def research_run(record: ResearchRunRecord | RunEnded) -> ResearchRun:
    run = record.run if isinstance(record, RunEnded) else record
    values = {**asdict(run), "sources": _urls(run.sources) or None}
    return ResearchRun.model_validate(values)


def kpis(view: KpiView) -> DashboardKpis:
    return DashboardKpis.model_validate(view, from_attributes=True)


def insights(view: InsightsView) -> JobInsights:
    return JobInsights.model_validate(asdict(view))


def _urls(values: Sequence[str]) -> list[Source1]:
    """Keep only well-formed URLs; a malformed stored source is dropped, not a 500."""
    urls: list[Source1] = []
    for value in values:
        try:
            urls.append(Source1.model_validate(value))
        except ValidationError:
            continue
    return urls


def rejection_analysis(record: RejectionRecord) -> RejectionAnalysis:
    return RejectionAnalysis(
        role_id=record.role_id,
        version=record.version,
        reason_categories=[RejectionCategory(value) for value in record.reason_categories],
        summary=record.summary,
        lesson=record.lesson,
        use_in_kits=record.use_in_kits,
    )


def rejection_patterns(patterns: RejectionPatternsView) -> RejectionPatterns:
    return RejectionPatterns(
        total=patterns.total,
        analysed=patterns.analysed,
        categories=[
            Category(category=RejectionCategory(name), count=count)
            for name, count in patterns.categories
        ],
        lessons=[Lesson(lesson) for lesson in patterns.lessons],
    )
