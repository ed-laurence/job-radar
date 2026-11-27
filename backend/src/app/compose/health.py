"""Health area: the maintainer health panel (spec tag Health)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.db.engine import session_factory
from app.adapters.db.health import PgHealth
from app.adapters.fakes.data import InMemoryUsageStore, InMemoryWebEvents
from app.adapters.fakes.health import MemoryHealth
from app.api import health as health_routes
from app.domain.health import HealthService
from app.domain.ports.health import HealthSource

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.main import Container


def health_source(container: Container) -> HealthSource:
    if container.engine is not None:
        return PgHealth(session_factory(container.engine))
    usage = container.usage if isinstance(container.usage, InMemoryUsageStore) else None
    web = container.web_events if isinstance(container.web_events, InMemoryWebEvents) else None
    return MemoryHealth(usage, web)


def install(app: FastAPI, container: Container) -> None:
    source = health_source(container)
    app.state.health_source = source  # tests reach the fake's job runs
    app.state.health = HealthService(
        source,
        container.clock,
        container.settings.llm_budget_month_aud,
        budgets=container.user_settings(),
        usd_to_aud=container.settings.usd_to_aud,
    )
    app.include_router(health_routes.router)
