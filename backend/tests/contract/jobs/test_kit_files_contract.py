"""Kit downloads (`getKitCoverLetterPdf`, `getKitCvPdf`): routed as the spec says, PDFs with an
attachment or inline Content-Disposition and the rate-limit headers, 404 problems otherwise.
Offline (memory stores); fictional data."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import cast

import httpx
import yaml
from fastapi import FastAPI
from tests.conftest import MAINTAINER, USER, auth
from tests.pdfs import b64, make_pdf

from app.compose.jobs import JobsRuntime
from app.domain.identity import derive_user_id
from app.main import Container, build_app
from app.settings import Settings

ME, THEM = auth(MAINTAINER), auth(USER)
OPERATIONS = {
    "getKitCoverLetterPdf": "/api/roles/{roleId}/kit/cover-letter.pdf",
    "getKitCvPdf": "/api/roles/{roleId}/kit/cv.pdf",
}


def test_operations_are_routed_as_specified(settings: Settings, container: Container) -> None:
    app = build_app(container)
    spec = cast("dict[str, object]", yaml.safe_load(settings.openapi_path.read_text("utf-8")))
    paths = cast("dict[str, dict[str, dict[str, object]]]", spec["paths"])
    routed = cast("dict[str, dict[str, dict[str, object]]]", app.openapi()["paths"])
    for operation_id, path in OPERATIONS.items():
        specified = paths[path.removeprefix("/api")]["get"]
        assert routed[path]["get"]["operationId"] == specified["operationId"] == operation_id
        assert specified["x-rate-limit-tier"] == "default"
        params = cast("list[dict[str, object]]", specified["parameters"])
        assert [p["name"] for p in params] == ["download"]


async def test_downloads(container: Container) -> None:
    app: FastAPI = build_app(container)
    runtime = app.state.jobs_runtime
    assert isinstance(runtime, JobsRuntime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as http:
        body = {"title": "Lead Data Engineer", "company": "Fabrikam Freight - Logistics"}
        role_id = (await http.post("/api/roles", json=body, headers=ME)).json()["id"]
        letter = f"/api/roles/{role_id}/kit/cover-letter.pdf"
        cv = f"/api/roles/{role_id}/kit/cv.pdf"
        for path in (letter, cv):
            missing = await http.get(path, headers=ME)
            assert missing.status_code == 404
            assert missing.headers["content-type"].startswith("application/problem+json")

        values = {"generated_at": datetime.now(UTC), "cover_note": "**Why me.** I ran it."}
        await runtime.stores.roles.save_kit(derive_user_id(MAINTAINER), uuid.UUID(role_id), values)
        pdf = make_pdf()
        upload = {"filename": "cv.pdf", "data": b64(pdf)}
        assert (
            await http.put("/api/profile/documents/leadership", headers=ME, json=upload)
        ).status_code == 200

        got = await http.get(letter, headers=ME)
        assert got.status_code == 200
        assert got.content.startswith(b"%PDF-")
        assert got.headers["content-disposition"].startswith(
            'attachment; filename="Maintainer_Cover_Letter_Fabrikam_Freight.pdf"'
        )
        assert got.headers["ratelimit-limit"]
        inline = await http.get(letter, headers=ME, params={"download": "false"})
        assert inline.headers["content-disposition"].startswith("inline;")
        cv_got = await http.get(cv, headers=ME)  # technical wanted; leadership is what exists
        assert (cv_got.status_code, cv_got.content) == (200, pdf)
        assert (await http.get(letter, headers=THEM)).status_code == 404
        assert (await http.get(cv, headers=THEM)).status_code == 404
