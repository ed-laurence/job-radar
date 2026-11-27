"""What the job-search routes get from composition, auth dependencies per rate tier, and the
plain-value conversion of generated request models."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, cast

from fastapi import Depends, Query, Request, Response
from pydantic import AnyUrl, BaseModel, RootModel

from app.agents.research.service import ResearchService
from app.api.security import rate_limited_user
from app.domain.identity import Principal
from app.domain.jobs.campaign import CampaignService
from app.domain.jobs.chat import ChatService
from app.domain.jobs.crm import ContactService, TargetService
from app.domain.jobs.dashboard import DashboardService
from app.domain.jobs.documents import ProfileDocumentService
from app.domain.jobs.evidence_test import EvidenceTestService
from app.domain.jobs.experience import ExperienceService
from app.domain.jobs.followups import FollowupService, FollowupStream
from app.domain.jobs.kit_files import KitFileService
from app.domain.jobs.kits import KitService
from app.domain.jobs.linkedin import LinkedinImportService
from app.domain.jobs.main_page import MainPageService
from app.domain.jobs.rejections import RejectionService
from app.domain.jobs.roles import RoleService
from app.domain.jobs.scoring import ScoringService
from app.domain.rate_limits import RateTier


@dataclass(frozen=True)
class JobsServices:
    roles: RoleService
    scoring: ScoringService
    contacts: ContactService
    targets: TargetService
    kits: KitService
    chat: ChatService
    research: ResearchService
    dashboard: DashboardService
    rejections: RejectionService
    followups: FollowupService
    followup_stream: FollowupStream
    campaign: CampaignService
    main_page: MainPageService
    documents: ProfileDocumentService
    linkedin: LinkedinImportService
    experience: ExperienceService
    evidence: EvidenceTestService
    kit_files: KitFileService


def rate_limit_headers(sent: Response) -> dict[str, str]:
    """The RateLimit-* headers the auth dependency set on the injected `Response`. FastAPI
    drops them when a route returns its own Response, so file routes copy them across."""
    return {k: v for k, v in sent.headers.items() if k.lower().startswith("ratelimit-")}


def jobs(request: Request) -> JobsServices:
    found = request.app.state.jobs
    assert isinstance(found, JobsServices)
    return found


DefaultUser = Annotated[Principal, Depends(rate_limited_user(RateTier.DEFAULT))]
AiUser = Annotated[Principal, Depends(rate_limited_user(RateTier.AI))]
AgentUser = Annotated[Principal, Depends(rate_limited_user(RateTier.AGENT))]
StreamUser = Annotated[Principal, Depends(rate_limited_user(RateTier.STREAM))]

Cursor = Annotated[str | None, Query(max_length=512)]
Limit = Annotated[int, Query(ge=1, le=100)]
Version = Annotated[int, Query(ge=1)]


def plain(value: object) -> object:
    """Generated-model values to plain Python: enums, URLs and root models become their value."""
    if isinstance(value, RootModel):
        return plain(cast("RootModel[object]", value).root)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, AnyUrl):
        return str(value)
    if isinstance(value, list | tuple):
        return [plain(item) for item in cast("Sequence[object]", value)]
    return value


def fields_of(model: BaseModel, *, exclude: frozenset[str] = frozenset()) -> dict[str, object]:
    """The fields the client actually sent (PATCH semantics), as plain values."""
    return {
        name: plain(getattr(model, name)) for name in model.model_fields_set if name not in exclude
    }
