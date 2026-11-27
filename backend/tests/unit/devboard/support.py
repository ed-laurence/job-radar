"""Helpers for dev-board tests: an app on the shared fake container, plus its runtime so a
test can wait for background work (triage, plans, simulated runs)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

from app.compose.devboard import DevboardRuntime
from app.main import Container, build_app
from tests.conftest import MAINTAINER, USER, auth
from tests.unit.devboard.cards import Card

AS_MAINTAINER = auth(MAINTAINER)
AS_USER = auth(USER)


@dataclass
class Board:
    http: httpx.AsyncClient
    runtime: DevboardRuntime

    async def settle(self) -> None:
        await self.runtime.dispatcher.drain()

    async def create(
        self, card: Card, headers: dict[str, str] = AS_MAINTAINER
    ) -> dict[str, object]:
        response = await self.http.post(
            "/api/tasks", json={"title": card.title, "body": card.body}, headers=headers
        )
        assert response.status_code == 201, response.text
        await self.settle()
        return await self.task(str(response.json()["id"]))

    async def task(self, task_id: str) -> dict[str, object]:
        response = await self.http.get(f"/api/tasks/{task_id}", headers=AS_MAINTAINER)
        assert response.status_code == 200, response.text
        body: dict[str, object] = response.json()
        return body

    async def move(
        self,
        task: dict[str, object],
        column: str,
        rank: str = "U",
        headers: dict[str, str] = AS_MAINTAINER,
        reason: str | None = None,
    ) -> httpx.Response:
        payload: dict[str, object] = {"to_column": column, "rank": rank, "version": task["version"]}
        if reason is not None:
            payload["reason"] = reason
        response = await self.http.post(
            f"/api/tasks/{task['id']}/move", json=payload, headers=headers
        )
        await self.settle()
        return response


@asynccontextmanager
async def board(container: Container) -> AsyncGenerator[Board]:
    app = build_app(container)
    runtime = app.state.devboard_runtime
    assert isinstance(runtime, DevboardRuntime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as http:
        yield Board(http=http, runtime=runtime)
