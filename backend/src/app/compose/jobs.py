"""Wires the job-search area: stores (Postgres, or in-memory without a database), the LLM,
the redaction filter, the services and the routes."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.adapters.campaign_plan import YamlPlanSource
from app.adapters.db.engine import session_factory
from app.adapters.db.evidence import PgEvidence
from app.adapters.db.jobs_campaign import PgCampaign
from app.adapters.db.jobs_dashboard import PgDashboard
from app.adapters.db.jobs_roles import PgRoles
from app.adapters.db.jobs_stores import PgChats, PgFollowups, PgProfiles, PgResearch, PgRows
from app.adapters.db.profile_documents import PgManifests
from app.adapters.db.profile_stores import PgContactImport, PgExperience
from app.adapters.db.tables_jobs import Contact, Target
from app.adapters.fakes.data import InMemoryUsageStore
from app.adapters.fakes.evidence import MemoryEvidence
from app.adapters.fakes.jobs import (
    MemoryChats,
    MemoryFollowups,
    MemoryProfiles,
    MemoryResearch,
    MemoryRoles,
    MemoryRows,
)
from app.adapters.fakes.jobs_campaign import MemoryCampaign
from app.adapters.fakes.jobs_dashboard import MemoryDashboard
from app.adapters.fakes.profile import MemoryContactImport, MemoryExperience
from app.adapters.fakes.system import InMemoryStorage
from app.adapters.http import GoogleAccessTokens, allowed_hosts, allowlisted_client
from app.adapters.pdf import PypdfText
from app.adapters.pdf_letter import ReportlabLetters
from app.adapters.storage_gcs import GCS_HOST, GcsStorage
from app.adapters.storage_local import LocalStorage
from app.agents.research.agent import ResearchAgent, ResearchConfig
from app.agents.research.service import ResearchService
from app.agents.research.tools import KnownDataLookup
from app.api.jobs import routers
from app.api.jobs.deps import JobsServices
from app.compose.health import health_source
from app.domain.errors import Forbidden
from app.domain.identity import Allowlist, Principal
from app.domain.jobs.campaign import CampaignService
from app.domain.jobs.chat import ChatService
from app.domain.jobs.crm import ContactService, TargetService
from app.domain.jobs.dashboard import DashboardService
from app.domain.jobs.documents import ManifestStore, ProfileDocumentService
from app.domain.jobs.evidence_test import EvidenceStore, EvidenceTestService
from app.domain.jobs.experience import ExperienceService, ExperienceStore
from app.domain.jobs.followups import FollowupService, FollowupStream
from app.domain.jobs.kit_files import KitFileService
from app.domain.jobs.kits import KitService
from app.domain.jobs.linkedin import ContactImportStore, LinkedinImportService
from app.domain.jobs.main_page import MainPageService
from app.domain.jobs.ports import (
    CallFact,
    CampaignStore,
    ChatStore,
    ContactStore,
    DashboardSource,
    FilterEvent,
    FollowupStore,
    ProfileStore,
    Released,
    ResearchStore,
    Retracted,
    RoleScorer,
    TargetStore,
)
from app.domain.jobs.profiles import ProfileWithSettings
from app.domain.jobs.records import ContactRecord, TargetRecord
from app.domain.jobs.rejections import RejectionService
from app.domain.jobs.roles import RoleService
from app.domain.jobs.scoring import ScoringService
from app.domain.ports import LLM, ServerTool, Storage
from app.guards.redaction import Redactor, SegmentReleased, StreamEvent, StreamingRedactor
from app.settings import REPO_ROOT

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.main import Container

# Job-search replay fixtures live in their own directory under the shared fixtures root.
FIXTURES_SUBDIR = "jobs"


class _Segments:
    """`app.guards.redaction.StreamingRedactor` behind the domain's `SegmentFilter` port."""

    def __init__(self, redactor: Redactor) -> None:
        self._stream = StreamingRedactor(redactor)

    @property
    def visible_text(self) -> str:
        return self._stream.visible_text

    async def feed(self, chunk: str) -> list[FilterEvent]:
        return [_event(item) for item in await self._stream.feed(chunk)]

    async def finish(self) -> list[FilterEvent]:
        return [_event(item) for item in await self._stream.finish()]


def _event(item: StreamEvent) -> FilterEvent:
    if isinstance(item, SegmentReleased):
        return Released(item.segment_id, item.text)
    return Retracted(item.segment_id, item.reason.value)


class GuardRedaction:
    def __init__(self, redactor: Redactor) -> None:
        self._redactor = redactor

    def open_stream(self) -> _Segments:
        return _Segments(self._redactor)

    async def redact_step(self, text: str) -> str | None:
        return await self._redactor.redact_or_none(text)


# Uploaded profile documents in development (git-ignored). Production uses GCS behind the same
# `Storage` port.
LOCAL_STORAGE_ROOT = REPO_ROOT / ".artifacts" / "storage"


@dataclass(frozen=True)
class JobStores:
    roles: PgRoles | MemoryRoles
    contacts: ContactStore
    targets: TargetStore
    chats: ChatStore
    followups: FollowupStore
    research: ResearchStore
    profiles: ProfileStore
    dashboard: DashboardSource
    campaign: CampaignStore
    experience: ExperienceStore
    contact_import: ContactImportStore
    storage: Storage
    evidence: EvidenceStore
    manifests: ManifestStore | None = None


def object_storage(container: Container) -> Storage:
    """`STORAGE=gcs` (+ `STORAGE_BUCKET`) in prod; local disk otherwise."""
    settings = container.settings
    if settings.storage == "local":
        return LocalStorage(LOCAL_STORAGE_ROOT)
    if not settings.storage_bucket:
        raise ValueError("STORAGE=gcs needs STORAGE_BUCKET")
    http = allowlisted_client(allowed_hosts(GCS_HOST), timeout_s=30.0)
    container.http.append(http)
    return GcsStorage(http, GoogleAccessTokens(), settings.storage_bucket)


def job_stores(container: Container) -> JobStores:
    if container.engine is not None:
        sessions = session_factory(container.engine)
        return JobStores(
            roles=PgRoles(sessions),
            contacts=PgRows(sessions, Contact, ContactRecord, "url"),
            targets=PgRows(sessions, Target, TargetRecord, "name"),
            chats=PgChats(sessions),
            followups=PgFollowups(sessions),
            research=PgResearch(sessions),
            profiles=PgProfiles(sessions),
            dashboard=PgDashboard(sessions),
            campaign=PgCampaign(sessions),
            experience=PgExperience(sessions),
            contact_import=PgContactImport(sessions),
            storage=object_storage(container),
            evidence=PgEvidence(sessions),
            manifests=PgManifests(sessions),
        )
    roles = MemoryRoles()
    contacts = MemoryRows(ContactRecord, "url")
    experience = MemoryExperience()
    return JobStores(
        roles=roles,
        contacts=contacts,
        experience=experience,
        contact_import=MemoryContactImport(contacts),
        storage=InMemoryStorage(),
        evidence=MemoryEvidence(),
        targets=MemoryRows(TargetRecord, "name"),
        chats=MemoryChats(),
        followups=MemoryFollowups(),
        research=MemoryResearch(publisher=container.offline_hub),
        profiles=MemoryProfiles(experience),
        dashboard=MemoryDashboard(roles, _usage_calls(container)),
        campaign=MemoryCampaign(roles, contacts),
    )


def _usage_calls(container: Container) -> _UsageCalls | None:
    usage = container.usage
    return _UsageCalls(usage) if isinstance(usage, InMemoryUsageStore) else None


class _UsageCalls:
    def __init__(self, usage: InMemoryUsageStore) -> None:
        self._usage = usage

    def __call__(self, user_id: uuid.UUID) -> Sequence[CallFact]:
        return [
            CallFact(
                at=call.at,
                feature=call.feature.value,
                provider=call.provider,
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                cost_usd=call.cost_usd,
            )
            for call in self._usage.calls
            if call.user_id == user_id
        ]


def jobs_llm(container: Container) -> LLM:
    """The shared LLM adapter. Under replay it also finds this area's fixtures in
    `<fixtures>/jobs/` (ReplayProvider searches each area folder)."""
    return container.llm


def research_config(container: Container) -> ResearchConfig:
    """Web search is the provider's server tool, offered only when `RESEARCH_WEB_SEARCH` is on;
    the app never runs its own search (SEC-10)."""
    is_on = container.settings.research_web_search
    return ResearchConfig(server_tools=(ServerTool("web_search"),) if is_on else ())


def document_service(container: Container, stores: JobStores) -> ProfileDocumentService:
    """Profile documents (uploads and the seed's demo CVs take the same path)."""
    return ProfileDocumentService(
        stores.storage,
        PypdfText(),
        container.guard,
        container.pii,
        container.clock,
        container.ids,
        stores.manifests,
    )


def build_services(container: Container, stores: JobStores | None = None) -> JobsServices:
    stores = stores or job_stores(container)
    clock, ids, llm = container.clock, container.ids, jobs_llm(container)
    redaction = GuardRedaction(
        Redactor(container.settings.denylist_values(), container.pii, container.guard)
    )
    prefs = container.user_settings()
    documents = document_service(container, stores)
    profiles = ProfileWithSettings(stores.profiles, prefs, documents)
    roles = RoleService(stores.roles, container.guard, clock, ids, prefs)
    targets = TargetService(stores.targets, clock, ids)
    tools = [KnownDataLookup(stores.roles, stores.contacts)]
    agent = ResearchAgent(
        llm, stores.research, tools, redaction, clock, ids, research_config(container)
    )
    followups = FollowupService(roles, stores.contacts, stores.followups, llm, ids)
    return JobsServices(
        roles=roles,
        scoring=ScoringService(stores.roles, roles, profiles, llm, clock, ids, prefs),
        contacts=ContactService(stores.contacts, clock, ids),
        targets=targets,
        kits=KitService(roles, stores.roles, profiles, llm, redaction, clock),
        chat=ChatService(roles, stores.chats, profiles, llm, redaction, clock, ids),
        research=ResearchService(
            targets, stores.research, agent, clock, ids, hub=container.step_hub()
        ),
        dashboard=DashboardService(
            stores.dashboard, clock, stores.campaign, prefs, container.settings.usd_to_aud
        ),
        rejections=RejectionService(roles, stores.roles, llm, clock),
        followups=followups,
        followup_stream=FollowupStream(followups, redaction),
        campaign=CampaignService(
            YamlPlanSource(container.settings.campaign_config),
            stores.campaign,
            roles,
            clock,
            ids,
            prefs,
        ),
        main_page=MainPageService(health_source(container), stores.campaign, clock, prefs),
        documents=documents,
        linkedin=LinkedinImportService(stores.contact_import, documents, clock, ids),
        experience=ExperienceService(
            stores.experience, clock, ids, usage=stores.evidence, cvs=documents, roles=roles
        ),
        kit_files=KitFileService(
            roles=roles,
            profiles=stores.profiles,
            cvs=documents,
            renderer=ReportlabLetters(),
            clock=clock,
            prefs=prefs,
        ),
        evidence=EvidenceTestService(
            role_service=roles,
            roles=stores.roles,
            profiles=profiles,
            experience=stores.experience,
            store=stores.evidence,
            llm=llm,
            clock=clock,
            prefs=prefs,
        ),
    )


@dataclass(frozen=True)
class JobsRuntime:
    """The area's services and the stores behind them (jobs and tests reach the stores)."""

    services: JobsServices
    stores: JobStores


def install(app: FastAPI, container: Container) -> None:
    stores = job_stores(container)
    services = build_services(container, stores)
    app.state.jobs = services
    app.state.jobs_runtime = JobsRuntime(services, stores)
    for router in routers():
        app.include_router(router)


class ScoringRoleScorer:
    """`RoleScorer` over the scoring service, acting as the role's owner (RLS scope and LLM
    attribution). An owner who is no longer on the allowlist gets nothing scored."""

    def __init__(self, scoring: ScoringService, allowlist: Allowlist) -> None:
        self._scoring = scoring
        self._allowlist = allowlist

    async def score_role(self, user_id: uuid.UUID, role_id: uuid.UUID) -> None:
        entry = next((e for e in self._allowlist.entries if e.user_id == user_id), None)
        if entry is None:
            raise Forbidden("That account is no longer allowed.")
        principal = Principal(user_id=user_id, email=entry.email, role=entry.role)
        await self._scoring.score_role(principal, role_id)


def role_scorer(container: Container, stores: JobStores | None = None) -> RoleScorer:
    """The jobs area's scoring, for scheduled jobs (`app.jobs.scan`)."""
    services = build_services(container, stores)
    return ScoringRoleScorer(services.scoring, container.settings.allowlist())
