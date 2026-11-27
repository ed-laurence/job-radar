"""The full evidence test (`getRoleEvidence`, `runRoleEvidence`): routed as the spec says, the
JSON validates against the generated model, quotes are verified, the role's evidence summary
follows, other users get 404, and "Run again" is on the AI rate tier. Replay fixture only;
the candidate material is fictional."""

from __future__ import annotations

from typing import cast

import httpx
import pytest
import yaml
from fastapi import FastAPI
from tests.conftest import MAINTAINER, USER, auth

from app.generated.models import ExperienceList, ExperienceRecord, Role, RoleEvidenceTest
from app.main import Container, build_app
from app.settings import Settings

ME = auth(MAINTAINER)
THEM = auth(USER)
AD = (
    "Fabrikam Freight is hiring a Lead Platform Engineer to lead a team of 6. Essential: "
    "observability at scale, Terraform, and on-call leadership."
)
RECORD = {
    "requirement": "Observability at scale",
    "entries": [
        {
            "context": "Platform lead, Tailspin Toys",
            "detail": "Led the platform team of 4 through a cloud move.",
        },
        {
            "context": "Personal project",
            "detail": "Rolled out OpenTelemetry tracing across 40 services.",
        },
    ],
}
QUOTED = "Rolled out OpenTelemetry tracing across 40 services."
OPERATIONS = {
    "getRoleEvidence": ("get", "/api/roles/{roleId}/evidence", "default"),
    "runRoleEvidence": ("post", "/api/roles/{roleId}/evidence", "ai"),
}


@pytest.fixture
def app(container: Container) -> FastAPI:
    return build_app(container)


@pytest.fixture
async def http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test")


def test_operations_are_routed_as_specified(settings: Settings, app: FastAPI) -> None:
    spec = cast("dict[str, object]", yaml.safe_load(settings.openapi_path.read_text("utf-8")))
    paths = cast("dict[str, dict[str, dict[str, object]]]", spec["paths"])
    routed = cast("dict[str, dict[str, dict[str, object]]]", app.openapi()["paths"])
    for operation_id, (method, path, tier) in OPERATIONS.items():
        mine = routed[path][method]
        specified = paths[path.removeprefix("/api")][method]
        assert mine["operationId"] == specified["operationId"] == operation_id
        assert specified["x-rate-limit-tier"] == tier
        assert "200" in cast("dict[str, object]", mine["responses"])


async def _role(http: httpx.AsyncClient, description: str | None = AD) -> str:
    body: dict[str, object] = {"title": "Lead Platform Engineer", "company": "Fabrikam Freight"}
    if description is not None:
        body["description"] = description
    created = await http.post("/api/roles", json=body, headers=ME)
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def test_run_then_read_the_evidence_test(http: httpx.AsyncClient) -> None:
    saved = await http.post("/api/experience", json=RECORD, headers=ME)
    assert saved.status_code == 201, saved.text
    record = ExperienceRecord.model_validate(saved.json())
    role_id = await _role(http)
    path = f"/api/roles/{role_id}/evidence"

    never = await http.get(path, headers=ME)
    assert never.status_code == 404
    assert never.json()["type"] == "/problems/not-found"

    ran = await http.post(path, headers=ME)
    assert ran.status_code == 200, ran.text
    body = RoleEvidenceTest.model_validate(ran.json())
    assert [(i.requirement, i.match.value, i.source.value) for i in body.items] == [
        ("Observability at scale", "shown", "records"),
        ("Terraform", "partial", "none"),
        ("On-call leadership", "missing", "none"),
    ]
    shown = body.items[0]
    assert shown.quote == QUOTED
    assert shown.record_id == record.id
    assert shown.record_title == "Observability at scale"
    assert body.items[1].quote is None  # a paraphrase isn't a quote
    assert "quote" not in ran.json()["items"][1]
    assert (body.shown, body.total, body.from_records) == (1, 3, 1)
    assert body.verdict.value == "skip"
    assert body.verdict_text == (
        "Skip · you show 1 of 3 must-haves (1 from your records, not on your CV)"
    )
    assert body.team_check.status.value == "ok"
    assert body.team_check.text == "Team: within twice what you've led."

    latest = await http.get(path, headers=ME)
    assert latest.status_code == 200
    assert RoleEvidenceTest.model_validate(latest.json()) == body

    role = Role.model_validate((await http.get(f"/api/roles/{role_id}", headers=ME)).json())
    assert role.evidence is not None
    assert (role.evidence.shown, role.evidence.must_haves_total) == (1, 3)
    assert role.evidence.summary == body.verdict_text

    listed = ExperienceList.model_validate((await http.get("/api/experience", headers=ME)).json())
    assert [(item.id, item.uses) for item in listed.items] == [(record.id, 1)]


async def test_other_users_get_not_found(http: httpx.AsyncClient) -> None:
    path = f"/api/roles/{await _role(http)}/evidence"
    assert (await http.get(path, headers=THEM)).status_code == 404
    assert (await http.post(path, headers=THEM)).status_code == 404


async def test_run_again_is_rate_limited_on_the_ai_tier(http: httpx.AsyncClient) -> None:
    path = f"/api/roles/{await _role(http, description=None)}/evidence"
    statuses = [(await http.post(path, headers=ME)).status_code for _ in range(7)]
    assert statuses[:6] == [422] * 6  # no ad text: refused before any model call
    limited = await http.post(path, headers=ME)
    assert statuses[6] == limited.status_code == 429
    assert "Retry-After" in limited.headers
