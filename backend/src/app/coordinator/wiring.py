"""Composition for the coordinator process: settings to stores, guards and services.

The coordinator is its own process (not `app.main`): a 2-connection pool as the restricted app
login (RLS enforced, checked at start-up), no LISTEN connection (step inserts NOTIFY through the
`run_steps` trigger), and only the guards the redaction filter needs.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.adapters.db.devboard_leases import PgLeaseStore
from app.adapters.db.devboard_runs import PgRunStore
from app.adapters.db.devboard_tasks import PgTaskStore
from app.adapters.db.engine import (
    assert_rls_enforced,
    create_engine,
    session_factory,
    set_user,
    system_transaction,
)
from app.adapters.db.notify import PgNotificationHub
from app.adapters.fakes.security import FakeContentGuard, FakePII
from app.adapters.http import GoogleAccessTokens, allowed_hosts, allowlisted_client
from app.adapters.system import SystemClock, UuidIds
from app.coordinator.config import DB_POOL_SIZE, CoordinatorSettings
from app.coordinator.service import CoordinatorService, Services, Stores
from app.domain.devboard.board import BoardService
from app.domain.devboard.leases import LeaseService
from app.domain.devboard.runs import RunService
from app.domain.ports import PII, Clock, ContentGuard
from app.guards.gcp import ModelArmorGuard, SdpPII, model_armor_host
from app.guards.redaction import Redactor

# The board tables are shared (any signed-in account sees them), so a maintainer's scope can read
# a task's owner; every later query runs as that owner.
_LOOKUP_SCOPE = text("SELECT id FROM users WHERE role = 'maintainer' ORDER BY id LIMIT 1")
_OWNER = text("SELECT created_by FROM dev_tasks WHERE id = :task_id AND deleted_at IS NULL")


class PgTaskOwners:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def owner_of(self, task_id: uuid.UUID) -> uuid.UUID | None:
        async with system_transaction(self._sessions) as session:
            scope = await session.scalar(_LOOKUP_SCOPE)
            if not isinstance(scope, uuid.UUID):
                return None
            await set_user(session, scope)
            owner = await session.scalar(_OWNER, {"task_id": task_id})
        return owner if isinstance(owner, uuid.UUID) else None


@dataclass
class CoordinatorRuntime:
    service: CoordinatorService
    redactor: Redactor
    key: bytes
    clock: Clock
    allowed_hosts: tuple[str, ...]
    engine: AsyncEngine | None = None
    http: list[httpx.AsyncClient] = field(default_factory=list[httpx.AsyncClient])

    async def start(self) -> None:
        if self.engine is not None:
            await assert_rls_enforced(self.engine)  # SEC-03: never serve as a superuser

    async def aclose(self) -> None:
        for client in self.http:
            await client.aclose()
        if self.engine is not None:
            await self.engine.dispose()


def _guards(settings: CoordinatorSettings, http: httpx.AsyncClient) -> tuple[ContentGuard, PII]:
    if settings.guards == "fake":
        return FakeContentGuard(), FakePII()
    project = settings.gcp_project
    templates = (
        settings.model_armor_template,
        settings.sdp_deidentify_template,
        settings.sdp_reidentify_template,
        settings.sdp_inspect_template,
    )
    if project is None or not all(templates):
        raise ValueError("GUARDS=gcp needs GCP_PROJECT, MODEL_ARMOR_TEMPLATE and SDP_* templates")
    tokens = GoogleAccessTokens()
    guard = ModelArmorGuard(
        http,
        tokens,
        project=project,
        location=settings.model_armor_location,
        template=str(settings.model_armor_template),
    )
    pii = SdpPII(
        http,
        tokens,
        project=project,
        location=settings.sdp_location,
        deidentify_template=str(settings.sdp_deidentify_template),
        reidentify_template=str(settings.sdp_reidentify_template),
        inspect_template=str(settings.sdp_inspect_template),
    )
    return guard, pii


def build_runtime(settings: CoordinatorSettings) -> CoordinatorRuntime:
    if settings.database_url is None or not settings.database_url.get_secret_value():
        raise ValueError("DATABASE_URL (the restricted app login) is required")
    key = settings.token_key()
    url = settings.database_url.get_secret_value()
    engine = create_engine(url, pool_size=DB_POOL_SIZE)
    sessions = session_factory(engine)
    http = allowlisted_client(allowed_hosts(model_armor_host(settings.model_armor_location)))
    guard, pii = _guards(settings, http)
    clock, ids = SystemClock(), UuidIds()
    redactor = Redactor(settings.denylist_values(), pii, guard)
    tasks, runs = PgTaskStore(sessions), PgRunStore(sessions)
    # Never started: the coordinator only writes steps; the insert trigger sends the NOTIFY.
    hub = PgNotificationHub(url)
    services = Services(
        board=BoardService(tasks, pii, clock, ids),
        runs=RunService(runs, tasks, hub, redactor, clock),
        leases=LeaseService(PgLeaseStore(sessions), clock),
        redactor=redactor,
    )
    service = CoordinatorService(
        Stores(owners=PgTaskOwners(sessions), tasks=tasks, runs=runs),
        services,
        clock,
        asyncio.sleep,
    )
    return CoordinatorRuntime(
        service=service,
        redactor=redactor,
        key=key,
        clock=clock,
        allowed_hosts=settings.coordinator_allowed_hosts,
        engine=engine,
        http=[http],
    )
