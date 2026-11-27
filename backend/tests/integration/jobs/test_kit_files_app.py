"""Kit downloads through the app on Postgres (RLS): each route returns a PDF for the owner,
404 when there's nothing to download, and 404 for another tenant's role. Fictional data."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from app.compose.jobs import JobsRuntime
from app.domain.identity import derive_user_id
from app.main import build_app, build_container
from app.settings import Settings
from tests.conftest import APP_USERS, MAINTAINER, USER, auth
from tests.integration.conftest import Database
from tests.pdfs import b64, make_pdf

pytestmark = pytest.mark.integration
ME, THEM = auth(MAINTAINER), auth(USER)
PROBLEM = "application/problem+json"


async def test_kit_downloads_on_postgres(database: Database, owner: asyncpg.Connection) -> None:
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
    app = build_app(container)
    runtime = app.state.jobs_runtime
    assert isinstance(runtime, JobsRuntime)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as http:
            body = {"title": "Lead Data Engineer", "company": f"Fabrikam {uuid.uuid4().hex[:6]}"}
            created = await http.post("/api/roles", json=body, headers=ME)
            assert created.status_code == 201, created.text
            role_id = created.json()["id"]
            letter, cv = (
                f"/api/roles/{role_id}/kit/{name}" for name in ("cover-letter.pdf", "cv.pdf")
            )

            for path in (letter, cv):  # nothing yet
                missing = await http.get(path, headers=ME)
                assert missing.status_code == 404
                assert missing.headers["content-type"].startswith(PROBLEM)

            values = {
                "generated_at": datetime.now(UTC),
                "cover_note": "Dear team,\n\n**Why me.** I ran the platform.",
            }
            await runtime.stores.roles.save_kit(
                derive_user_id(MAINTAINER), uuid.UUID(role_id), values
            )
            pdf = make_pdf()
            put = await http.put(
                "/api/profile/documents/technical",
                headers=ME,
                json={"filename": "cv.pdf", "data": b64(pdf)},
            )
            assert put.status_code == 200, put.text
            try:
                got = await http.get(letter, headers=ME)
                assert got.status_code == 200, got.text
                assert got.headers["content-type"] == "application/pdf"
                assert got.content.startswith(b"%PDF-")
                disposition = got.headers["content-disposition"]
                assert disposition.startswith(
                    'attachment; filename="Maintainer_Cover_Letter_Fabrikam'
                )
                assert "RateLimit-Limit" in got.headers
                inline = await http.get(letter, headers=ME, params={"download": "false"})
                assert inline.headers["content-disposition"].startswith("inline;")

                cv_got = await http.get(cv, headers=ME)
                assert cv_got.status_code == 200, cv_got.text
                assert cv_got.content == pdf
                assert 'filename="Maintainer_CV.pdf"' in cv_got.headers["content-disposition"]

                for path in (letter, cv):  # another tenant
                    theirs = await http.get(path, headers=THEM)
                    assert theirs.status_code == 404
                assert (await http.get(letter)).status_code == 401
            finally:
                await http.delete("/api/profile/documents/technical", headers=ME)
    finally:
        await container.aclose()
