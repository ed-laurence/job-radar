"""HTTP behaviour of the foundation routes, with every adapter faked."""

from __future__ import annotations

from datetime import timedelta

import httpx

from app.adapters.fakes.system import FakeClock
from tests.conftest import MAINTAINER, USER, auth, iso

SESSION = "abcdefghijklmnop1234"


async def test_healthz_is_public_and_at_the_root(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Content-Type-Options"] == "nosniff"


async def test_routes_live_under_api_prefix(client: httpx.AsyncClient) -> None:
    assert (await client.get("/me", headers=auth(USER))).status_code == 404
    assert (await client.get("/api/me", headers=auth(USER))).status_code == 200


async def test_me_returns_the_allowlisted_caller(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/me", headers=auth(MAINTAINER))
    body = response.json()
    assert body["email"] == MAINTAINER
    assert body["role"] == "maintainer"
    assert body["display_name"] == "Maintainer"
    assert response.headers["RateLimit-Limit"] == "120"


async def test_missing_token_is_401_problem(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/me")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["type"] == "/problems/unauthenticated"
    assert body["correlation_id"] == response.headers["X-Correlation-Id"]


async def test_serverless_authorization_header_is_not_a_user_token(
    client: httpx.AsyncClient,
) -> None:
    # Cloud Run checks this header itself; it must never authenticate a user.
    response = await client.get(
        "/api/me", headers={"X-Serverless-Authorization": f"Bearer fake:{USER}"}
    )
    assert response.status_code == 401


async def test_unknown_and_lookalike_emails_are_403(client: httpx.AsyncClient) -> None:
    cyrillic_e = chr(0x435)
    lookalikes = (f"us{cyrillic_e}r@example.test", f"{USER}.", f"x{USER}")
    for email in ("stranger@example.test", *lookalikes):
        header = f"Bearer fake:{email}".encode()
        response = await client.get("/api/me", headers={b"Authorization": header})
        assert response.status_code == 403, email
        assert response.json()["type"] == "/problems/forbidden"


async def test_email_match_is_case_insensitive_only(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/me", headers=auth(USER.upper()))).status_code == 200


async def test_unverified_email_is_403(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/me", headers=auth(f"{USER};unverified"))
    assert response.status_code == 403


async def test_non_fake_bearer_is_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/me", headers={"Authorization": "Bearer eyJhbGciOi.x.y"})
    assert response.status_code == 401


async def test_events_ingest_and_summary(client: httpx.AsyncClient, clock: FakeClock) -> None:
    now = clock.now()
    events = [
        {"type": "page_view", "route": "/roles/[roleId]", "at": iso(now), "session_id": SESSION},
        {
            "type": "product",
            "route": "/roles",
            "at": iso(now),
            "session_id": SESSION,
            "name": "role_created",
        },
        {
            "type": "web_vital",
            "route": "/",
            "at": iso(now),
            "session_id": SESSION,
            "metric": "LCP",
            "value": 3000,
            "rating": "poor",
        },
        # Too old: dropped, not stored.
        {
            "type": "page_view",
            "route": "/",
            "at": iso(now - timedelta(days=5)),
            "session_id": SESSION,
        },
    ]
    response = await client.post("/api/events", json={"events": events}, headers=auth(USER))
    assert response.status_code == 202
    assert response.json() == {"accepted": 3}

    summary = await client.get("/api/analytics/summary", headers=auth(MAINTAINER))
    assert summary.status_code == 200
    body = summary.json()
    assert body["views_by_page"] == [{"route": "/roles/[roleId]", "views": 1, "sessions": 1}]
    assert body["web_vitals"][0]["over_budget"] is True
    application = next(f for f in body["funnels"] if f["name"] == "application")
    assert application["steps"][0] == {"name": "role_created", "sessions": 1}


async def test_events_reject_ids_in_routes_and_unknown_fields(
    client: httpx.AsyncClient, clock: FakeClock
) -> None:
    base = {"type": "page_view", "at": iso(clock.now()), "session_id": SESSION}
    for event in ({**base, "route": "/roles/123"}, {**base, "route": "/", "email": "a@b.c"}):
        response = await client.post("/api/events", json={"events": [event]}, headers=auth(USER))
        assert response.status_code == 422
        body = response.json()
        assert body["type"] == "/problems/validation-failed"
        assert body["errors"][0]["pointer"].startswith("/events/0")
        assert "123" not in response.text


async def test_summary_is_maintainer_only(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/analytics/summary", headers=auth(USER))
    assert response.status_code == 403


async def test_summary_rejects_backwards_range(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/api/analytics/summary?from=2026-11-10&to=2026-11-01", headers=auth(MAINTAINER)
    )
    assert response.status_code == 400


async def test_body_over_limit_is_413(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/events", content=b"{" + b" " * 5000 + b"}", headers=auth(USER)
    )
    assert response.status_code == 413
    assert response.json()["type"] == "/problems/payload-too-large"


async def test_rate_limit_returns_429_with_retry_after(
    client: httpx.AsyncClient, clock: FakeClock
) -> None:
    event = {"type": "page_view", "route": "/", "at": iso(clock.now()), "session_id": SESSION}
    statuses = [
        (await client.post("/api/events", json={"events": [event]}, headers=auth(USER)))
        for _ in range(31)
    ]
    assert all(r.status_code == 202 for r in statuses[:30])
    assert statuses[30].status_code == 429
    assert int(statuses[30].headers["Retry-After"]) >= 1


async def test_correlation_id_is_echoed_when_well_formed(client: httpx.AsyncClient) -> None:
    good = await client.get("/healthz", headers={"X-Correlation-Id": "abc12345-def"})
    assert good.headers["X-Correlation-Id"] == "abc12345-def"
    bad = await client.get("/healthz", headers={"X-Correlation-Id": "<script>"})
    assert bad.headers["X-Correlation-Id"] != "<script>"
