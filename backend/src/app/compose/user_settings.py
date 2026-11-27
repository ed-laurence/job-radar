"""Settings area: the caller's settings (spec tag Settings) and the monthly LLM spend cap."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.api import user_settings as settings_routes
from app.compose.health import health_source
from app.domain.user_settings import MonthlySpendCap

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.main import Container


def install(app: FastAPI, container: Container) -> None:
    service = container.user_settings()
    app.state.user_settings = service
    container.llm.use_spend_cap(
        MonthlySpendCap(service, health_source(container), container.settings.usd_to_aud)
    )
    app.include_router(settings_routes.router)
