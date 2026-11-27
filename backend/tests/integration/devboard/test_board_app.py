"""The board app on Postgres: compose picks the Pg stores and the shared LISTEN hub."""

from __future__ import annotations

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from app.compose.devboard import DevboardRuntime
from app.domain.identity import derive_user_id
from app.main import build_app, build_container
from app.settings import Settings
from tests.conftest import APP_USERS, MAINTAINER, USER, auth
from tests.integration.conftest import Database
from tests.integration.devboard.cleanup import leave_no_rows
from tests.unit.devboard import cards

pytestmark = pytest.mark.integration
__all__ = ["leave_no_rows"]  # the autouse fixture


async def test_triage_on_postgres_end_to_end(database: Database, owner: asyncpg.Connection) -> None:
    for email, role in ((MAINTAINER, "maintainer"), (USER, "user")):
        await owner.execute(
            "INSERT INTO users (id, email, role) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            derive_user_id(email),
            email,
            role,
        )
    settings = Settings(
        environment="test", app_users=SecretStr(APP_USERS), database_url=SecretStr(database.app_url)
    )
    container = build_container(settings)
    assert container.hub is not None
    await container.hub.start()
    app = build_app(container)
    runtime = app.state.devboard_runtime
    assert isinstance(runtime, DevboardRuntime)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as http:
            body = {"title": cards.API_SEARCH.title, "body": cards.API_SEARCH.body}
            created = await http.post("/api/tasks", json=body, headers=auth(USER))
            assert created.status_code == 201, created.text
            await runtime.dispatcher.drain()
            task_id = created.json()["id"]
            task = (await http.get(f"/api/tasks/{task_id}", headers=auth(USER))).json()
            assert task["column"] == "triaged"
            runs = (await http.get(f"/api/tasks/{task_id}/runs", headers=auth(USER))).json()
            stream = await http.get(f"/api/runs/{runs['items'][0]['id']}/steps", headers=auth(USER))
            assert stream.text.rstrip().splitlines()[-3] == "event: done"
    finally:
        await container.aclose()
