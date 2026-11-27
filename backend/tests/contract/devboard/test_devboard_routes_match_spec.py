"""Every dev-board operation in `api/openapi.yaml` is served at the same method and path,
with the role and rate-limit rules the spec declares."""

from __future__ import annotations

from typing import cast

import httpx
import pytest
import yaml
from tests.conftest import USER, auth

from app.main import Container, build_app
from app.settings import Settings

TAGS = {"Dev board", "Runs"}
METHODS = {"get", "post", "put", "patch", "delete"}


def _spec_operations(settings: Settings) -> dict[str, tuple[str, str, dict[str, object]]]:
    spec = cast("dict[str, object]", yaml.safe_load(settings.openapi_path.read_text("utf-8")))
    found: dict[str, tuple[str, str, dict[str, object]]] = {}
    for path, item in cast("dict[str, dict[str, object]]", spec["paths"]).items():
        for method, raw in item.items():
            if method not in METHODS:
                continue
            operation = cast("dict[str, object]", raw)
            if TAGS & set(cast("list[str]", operation.get("tags", []))):
                found[str(operation["operationId"])] = (method, "/api" + path, operation)
    return found


def test_every_board_operation_is_served(container: Container) -> None:
    served = build_app(container).openapi()
    routes = {
        str(operation["operationId"]): (method, path)
        for path, item in served["paths"].items()
        for method, operation in item.items()
    }
    expected = _spec_operations(container.settings)
    assert len(expected) == 16
    for operation_id, (method, path, _) in expected.items():
        assert routes.get(operation_id) == (method, path), operation_id


async def test_maintainer_only_operations_refuse_users(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    task = (await client.post("/api/tasks", json={"title": "t"}, headers=auth(USER))).json()
    for operation_id, (method, path, operation) in _spec_operations(settings).items():
        if operation.get("x-required-role") != "maintainer":
            continue
        url = path.replace("{taskId}", task["id"])
        response = await client.request(method, url, json={}, headers=auth(USER))
        assert response.status_code == 403, operation_id


@pytest.mark.parametrize("path", ["/api/tasks", "/api/runs/00000000-0000-4000-8000-000000000001"])
async def test_board_routes_need_a_signed_in_user(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
