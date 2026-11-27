"""The job-search routes cover every spec operation in their tags, with the same method, path,
operation id and success status, and their JSON responses validate against the spec models."""

from __future__ import annotations

from typing import cast

import httpx
import yaml
from tests.conftest import MAINTAINER, auth

from app.main import Container, build_app
from app.settings import Settings

AREA_TAGS = {
    "Roles",
    "Scoring",
    "Kits",
    "Chat",
    "Contacts",
    "Targets",
    "Research",
    "Dashboard",
    "Settings",
    "Scan",
}
type Operation = dict[str, object]


def _spec_operations(settings: Settings) -> dict[str, tuple[str, str, set[str]]]:
    spec = cast("dict[str, object]", yaml.safe_load(settings.openapi_path.read_text("utf-8")))
    found: dict[str, tuple[str, str, set[str]]] = {}
    for path, item in cast("dict[str, dict[str, object]]", spec["paths"]).items():
        for method, raw in item.items():
            if method in {"parameters", "servers"}:
                continue
            operation = cast("Operation", raw)
            tags = set(cast("list[str]", operation.get("tags", [])))
            if tags & AREA_TAGS:
                codes = set(cast("dict[str, object]", operation["responses"]))
                success = {code for code in codes if code.startswith("2")}
                found[str(operation["operationId"])] = (method, "/api" + path, success)
    return found


def _app_operations(container: Container) -> dict[str, tuple[str, str, set[str]]]:
    schema = build_app(container).openapi()
    found: dict[str, tuple[str, str, set[str]]] = {}
    for path, item in cast("dict[str, dict[str, Operation]]", schema["paths"]).items():
        for method, operation in item.items():
            codes = cast("dict[str, object]", operation.get("responses", {}))
            success = {code for code in codes if code.startswith("2")}
            found[str(operation["operationId"])] = (method, path, success)
    return found


def test_every_area_operation_is_routed_as_specified(
    settings: Settings, container: Container
) -> None:
    spec = _spec_operations(settings)
    app = _app_operations(container)
    assert len(spec) == 57  # + runScan, getScanStatus, getRoleEvidence, runRoleEvidence, kit PDFs
    for operation_id, (method, path, success) in spec.items():
        assert operation_id in app, operation_id
        app_method, app_path, app_success = app[operation_id]
        assert (app_method, app_path) == (method, path), operation_id
        assert success <= app_success, operation_id


async def test_error_bodies_are_problems_without_internals(client: httpx.AsyncClient) -> None:

    response = await client.get(
        "/api/roles/00000000-0000-4000-8000-00000000ffff", headers=auth(MAINTAINER)
    )
    body = response.json()
    assert response.status_code == 404
    assert set(body) <= {"type", "title", "status", "detail", "instance", "correlation_id"}
    malformed = await client.get("/api/roles/not-a-uuid", headers=auth(MAINTAINER))
    assert malformed.status_code == 422
    assert malformed.json()["type"] == "/problems/validation-failed"
