"""Composition root: the only place that turns Settings into concrete adapters (ARCH-02/06).

`uvicorn app.main:create_app --factory`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from app import compose
from app.adapters.auth.identity_platform import IdentityPlatformVerifier
from app.adapters.db.engine import assert_rls_enforced, create_engine, session_factory
from app.adapters.db.notify import PgNotificationHub
from app.adapters.db.repositories import PgLlmUsageStore, PgRateLimiter, PgWebEvents
from app.adapters.db.user_settings import PgUserSettings
from app.adapters.fakes.data import (
    InMemoryNotificationHub,
    InMemoryUsageStore,
    InMemoryWebEvents,
)
from app.adapters.fakes.security import (
    FakeContentGuard,
    FakePII,
    FakeTokenVerifier,
    InMemoryRateLimiter,
)
from app.adapters.fakes.user_settings import MemoryUserSettings
from app.adapters.http import GoogleAccessTokens, allowed_hosts, allowlisted_client
from app.adapters.llm.providers.base import Provider
from app.adapters.llm.providers.replay import RecordingProvider, ReplayProvider
from app.adapters.llm.providers.synthetic import SyntheticProvider
from app.adapters.llm.providers.unconfigured import UnconfiguredProvider
from app.adapters.llm.providers.vertex_claude import AnthropicClaudeProvider, VertexClaudeProvider
from app.adapters.llm.providers.vertex_gemini import VertexGeminiProvider
from app.adapters.llm.providers.vertex_maas import VertexMaasProvider, maas_host
from app.adapters.llm.routing import load_routing
from app.adapters.llm.service import LlmService
from app.adapters.system import SystemClock, UuidIds
from app.api import problems
from app.api.context import ApiConfig, Services, install_middleware
from app.api.routes import api, root
from app.domain.analytics import AnalyticsService
from app.domain.ports import (
    PII,
    Clock,
    ContentGuard,
    Ids,
    LlmUsageStore,
    NotificationHub,
    RateLimiter,
    TokenVerifier,
    WebEventsRepository,
)
from app.domain.user_settings import SettingsService
from app.guards.gcp import ModelArmorGuard, SdpPII, model_armor_host
from app.settings import Settings


@dataclass
class Container:
    """Everything a process needs, built once. Jobs and the coordinator reuse it."""

    settings: Settings
    clock: Clock
    ids: Ids
    verifier: TokenVerifier
    rate_limiter: RateLimiter
    web_events: WebEventsRepository
    usage: LlmUsageStore
    guard: ContentGuard
    pii: PII
    llm: LlmService
    engine: AsyncEngine | None = None
    hub: PgNotificationHub | None = None
    http: list[httpx.AsyncClient] = field(default_factory=list[httpx.AsyncClient])
    # Without Postgres, in-memory stores publish step notifications here instead.
    offline_hub: InMemoryNotificationHub = field(default_factory=InMemoryNotificationHub)
    settings_service: SettingsService | None = None

    def user_settings(self) -> SettingsService:
        """Per-user settings, shared by every area that applies them (built on first use)."""
        if self.settings_service is None:
            store = (
                PgUserSettings(session_factory(self.engine))
                if self.engine is not None
                else MemoryUserSettings(self.clock)
            )
            self.settings_service = SettingsService(store, self.settings.llm_budget_month_aud)
        return self.settings_service

    def step_hub(self) -> NotificationHub:
        """The `run_steps` hub every step stream (board and research) listens on (ARCH-31)."""
        return self.hub if self.hub is not None else self.offline_hub

    async def aclose(self) -> None:
        if self.hub is not None:
            await self.hub.stop()
        for client in self.http:
            await client.aclose()
        if self.engine is not None:
            await self.engine.dispose()


def _verifier(settings: Settings) -> TokenVerifier:
    if settings.auth == "fake":
        return FakeTokenVerifier()
    if not settings.idp_project_id:
        raise ValueError("IDP_PROJECT_ID is required when AUTH=identity_platform")
    return IdentityPlatformVerifier(settings.idp_project_id, tenant_id=settings.idp_tenant_id)


def _guards(settings: Settings, http: httpx.AsyncClient) -> tuple[ContentGuard, PII]:
    if settings.guards == "fake":
        return FakeContentGuard(), FakePII()
    required = (
        settings.gcp_project,
        settings.model_armor_template,
        settings.sdp_deidentify_template,
        settings.sdp_reidentify_template,
        settings.sdp_inspect_template,
    )
    if not all(required):
        raise ValueError("GUARDS=gcp needs GCP_PROJECT, MODEL_ARMOR_TEMPLATE and SDP_* templates")
    tokens = GoogleAccessTokens()
    guard = ModelArmorGuard(
        http,
        tokens,
        project=str(settings.gcp_project),
        location=settings.model_armor_location,
        template=str(settings.model_armor_template),
    )
    pii = SdpPII(
        http,
        tokens,
        project=str(settings.gcp_project),
        location=settings.sdp_location,
        deidentify_template=str(settings.sdp_deidentify_template),
        reidentify_template=str(settings.sdp_reidentify_template),
        inspect_template=str(settings.sdp_inspect_template),
    )
    return guard, pii


def _live_providers(settings: Settings, http: httpx.AsyncClient) -> dict[str, Provider]:
    if settings.claude_backend == "anthropic":
        # Local only (prod validator refuses it). Without a GCP project the Model Garden and
        # Gemini routes fail as provider errors, so their routes fall back to Claude.
        if settings.anthropic_api_key is None:
            raise ValueError("ANTHROPIC_API_KEY is required when CLAUDE_BACKEND=anthropic")
        claude: Provider = AnthropicClaudeProvider(
            api_key=settings.anthropic_api_key.get_secret_value()
        )
        if not settings.gcp_project:
            return {
                "vertex-claude": claude,
                "vertex-maas": UnconfiguredProvider("vertex-maas"),
                "vertex-gemini": VertexGeminiProvider(),
            }
    elif not settings.gcp_project:
        raise ValueError("GCP_PROJECT is required when LLM=vertex or LLM=record")
    else:
        claude = VertexClaudeProvider(
            project_id=settings.gcp_project, region=settings.vertex_region
        )
    assert settings.gcp_project is not None
    return {
        "vertex-claude": claude,
        "vertex-maas": VertexMaasProvider(
            http, GoogleAccessTokens(), project=settings.gcp_project, region=settings.maas_region
        ),
        "vertex-gemini": VertexGeminiProvider(),
    }


def _providers(settings: Settings, http: httpx.AsyncClient) -> Mapping[str, Provider]:
    if settings.llm == "replay":
        on_miss = SyntheticProvider() if settings.llm_replay_miss == "synthesize" else None
        replay = ReplayProvider(settings.llm_fixtures_dir, on_miss=on_miss)
        return dict.fromkeys(("vertex-claude", "vertex-maas", "vertex-gemini"), replay)
    live = _live_providers(settings, http)
    if settings.llm == "record":
        return {
            name: RecordingProvider(provider, settings.llm_fixtures_dir)
            for name, provider in live.items()
        }
    return live


def build_container(settings: Settings) -> Container:
    clock, ids = SystemClock(), UuidIds()
    hosts = allowed_hosts(
        model_armor_host(settings.model_armor_location), maas_host(settings.maas_region)
    )
    http = allowlisted_client(hosts)
    engine = None
    hub = None
    if settings.database_url is not None:
        url = settings.database_url.get_secret_value()
        engine = create_engine(url, pool_size=settings.db_pool_size)
        sessions = session_factory(engine)
        rate_limiter: RateLimiter = PgRateLimiter(sessions, clock)
        web_events: WebEventsRepository = PgWebEvents(sessions)
        usage: LlmUsageStore = PgLlmUsageStore(sessions)
        hub = PgNotificationHub(url)
    else:
        if settings.environment == "prod":
            raise ValueError("DATABASE_URL is required in prod")
        rate_limiter, web_events, usage = (
            InMemoryRateLimiter(clock),
            InMemoryWebEvents(),
            InMemoryUsageStore(),
        )
    guard, pii = _guards(settings, http)
    llm = LlmService(
        load_routing(settings.models_config), _providers(settings, http), guard, pii, usage, clock
    )
    return Container(
        settings=settings,
        clock=clock,
        ids=ids,
        verifier=_verifier(settings),
        rate_limiter=rate_limiter,
        web_events=web_events,
        usage=usage,
        guard=guard,
        pii=pii,
        llm=llm,
        engine=engine,
        hub=hub,
        http=[http],
    )


def build_app(container: Container) -> FastAPI:
    settings = container.settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        if container.engine is not None:
            await assert_rls_enforced(container.engine)  # SEC-03: never serve as a superuser
        if container.hub is not None:
            await container.hub.start()
        # Local only: SCAN_INTERVAL_MINUTES runs the scan in-process (prod: Cloud Scheduler).
        scheduler = getattr(app.state, "scan_scheduler", None)
        if scheduler is not None:
            scheduler.start()
        # Local only, CODE_HOST=github: poll agent PRs (merged -> done).
        pr_sync = getattr(app.state, "pr_sync", None)
        if pr_sync is not None:
            pr_sync.start()
        try:
            yield
        finally:
            if pr_sync is not None:
                await pr_sync.stop()
            if scheduler is not None:
                await scheduler.stop()
            await container.aclose()

    app = FastAPI(
        title="Job Radar API",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.environment != "prod" else None,
    )
    app.state.services = Services(
        config=ApiConfig(
            ip_salt=settings.rate_limit_ip_salt.get_secret_value().encode(),
            trusted_proxy_hops=settings.trusted_proxy_hops,
            max_body_bytes=settings.max_body_bytes,
            rate_limit_scale=settings.rate_limit_scale,
        ),
        allowlist=settings.allowlist(),
        verifier=container.verifier,
        rate_limiter=container.rate_limiter,
        clock=container.clock,
        ids=container.ids,
        analytics=AnalyticsService(container.web_events, container.clock),
    )
    problems.install(app)
    install_middleware(app, container.ids, settings.max_body_bytes)
    app.include_router(root)
    app.include_router(api)
    compose.install_all(app, container)
    return app


def create_app() -> FastAPI:
    return build_app(build_container(Settings()))
