"""RFC 9457 mapping in `app.api.problems` on its own, without any area's composition."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from app.api import problems
from app.domain.errors import DomainError, IllegalTransition, PlanNotApproved


def _app_raising(error: DomainError) -> FastAPI:
    app = FastAPI()
    problems.install(app)

    @app.get("/boom")
    async def boom() -> None:
        raise error

    return app


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (IllegalTransition("No.", allowed=("backlog",)), "illegal-transition"),
        (PlanNotApproved("Approve the plan first."), "plan-not-approved"),
    ],
)
async def test_regression_rule_breaks_map_to_422_without_an_override(
    error: DomainError, code: str
) -> None:
    transport = httpx.ASGITransport(app=_app_raising(error))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")
    assert response.status_code == 422
    body = response.json()
    assert body["type"] == f"/problems/{code}"
    assert body["status"] == 422


async def test_illegal_transition_lists_allowed_columns_only_when_known() -> None:
    transport = httpx.ASGITransport(app=_app_raising(IllegalTransition("No.")))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get("/boom")).json()
    assert "allowed_columns" not in body
