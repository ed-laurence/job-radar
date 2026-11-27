"""Scan routes: maintainer only (403 for the tester), 409 while a scan runs, and responses that
validate against the spec models. Runs on the in-memory adapters with an empty seed pool."""

from __future__ import annotations

import httpx
from tests.conftest import MAINTAINER, USER, auth

from app.adapters.scrapers.seed_feed import MemorySeedFeed, PoolEntry
from app.domain.identity import derive_user_id
from app.domain.jobs.scan import ScanService
from app.generated.models import ScanRun, ScanStatus
from app.main import Container, build_app


def _app_and_service(container: Container) -> tuple[httpx.AsyncClient, ScanService]:
    app = build_app(container)
    service = app.state.scan
    assert isinstance(service, ScanService)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://api.test"), service


async def test_the_tester_gets_403(container: Container) -> None:
    client, _ = _app_and_service(container)
    async with client:
        assert (await client.post("/api/scan", headers=auth(USER))).status_code == 403
        assert (await client.get("/api/scan/status", headers=auth(USER))).status_code == 403


async def test_a_scan_returns_its_run_and_the_status_follows(container: Container) -> None:
    client, service = _app_and_service(container)
    feed = service.source
    assert isinstance(feed, MemorySeedFeed)
    owner = derive_user_id(MAINTAINER)
    feed.pool[owner] = [
        PoolEntry(external_id=f"seed-{n}", posted_at=None, payload={"title": f"Role {n}"})
        for n in range(3)
    ]
    async with client:
        response = await client.post("/api/scan", headers=auth(MAINTAINER))
        assert response.status_code == 200
        run = ScanRun.model_validate(response.json())
        assert (run.fetched, run.new, run.pool_remaining) == (3, 3, 0)
        assert run.trigger.value == "manual"
        status = await client.get("/api/scan/status", headers=auth(MAINTAINER))
        assert status.status_code == 200
        view = ScanStatus.model_validate(status.json())
        assert view.last_run is not None
        assert view.last_run.id == run.id
        assert (view.pool_remaining, view.running) == (0, False)
        roles = await client.get("/api/roles", headers=auth(MAINTAINER))
        assert {r["external_id"] for r in roles.json()["items"]} >= {"seed-0", "seed-1"}
        empty = ScanRun.model_validate(
            (await client.post("/api/scan", headers=auth(MAINTAINER))).json()
        )
        assert empty.status.value == "no_new_roles"


async def test_409_while_a_scan_is_running(container: Container) -> None:
    client, service = _app_and_service(container)
    async with client, service.lock.hold() as held:
        assert held
        response = await client.post("/api/scan", headers=auth(MAINTAINER))
        assert response.status_code == 409
        assert response.json()["type"] == "/problems/scan-running"
        status = await client.get("/api/scan/status", headers=auth(MAINTAINER))
        assert status.json()["running"] is True
