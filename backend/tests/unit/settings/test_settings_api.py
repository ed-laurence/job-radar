"""`GET`/`PUT /api/settings`: defaults, versioned replace with 409 on a stale write, the
maintainer-only LLM budget, validation, and per-user isolation (the app wired with fakes)."""

from __future__ import annotations

import copy

import httpx

from tests.conftest import MAINTAINER, USER, auth

ME = auth(MAINTAINER)
THEM = auth(USER)

DEFAULTS = {
    "profile": {"skills": "", "brief": ""},
    "scoring": {
        "keep_score": 55,
        "borderline_score": 45,
        "notify_score": 75,
        "notify_mission_score": 65,
    },
    "scoring_preferences": {
        "weights": {"skills": 50, "track_pay": 25, "location": 15, "freshness": 10},
        "pay_floors": {"perm_salary_aud": None, "contract_day_rate_aud": None},
        "work_modes": list[str](),
        "preferred_locations": list[str](),
    },
    "followups": {"after_hours": 24, "due_hours": 48, "nudge_days": 7},
    "kits": {"auto_kits": False, "kits_per_run": 5, "spelling": "en-AU"},
    "campaign": {"start_date": None},
    "archive_default_days": 14,
    "llm": {"monthly_budget_aud": None},
    "display": {"timezone": "Australia/Melbourne"},
    "version": 1,
    "updated_at": None,
}


def _update(current: dict[str, object], **changes: object) -> dict[str, object]:
    body = copy.deepcopy(current)
    body.pop("updated_at", None)
    body.update(changes)
    return body


async def test_unset_settings_read_as_the_defaults(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/settings", headers=ME)
    assert response.status_code == 200
    assert response.json() == DEFAULTS
    assert (await client.get("/api/settings")).status_code == 401


async def test_put_replaces_settings_and_bumps_the_version(client: httpx.AsyncClient) -> None:
    current = (await client.get("/api/settings", headers=ME)).json()
    body = _update(
        current,
        profile={"skills": "Python, SQL, data platforms", "brief": "Remote data lead roles"},
        scoring={
            "keep_score": 60,
            "borderline_score": 50,
            "notify_score": 80,
            "notify_mission_score": 70,
        },
        followups={"after_hours": 36, "due_hours": 72, "nudge_days": 5},
        kits={"auto_kits": True, "kits_per_run": 3, "spelling": "en-GB"},
        campaign={"start_date": "2026-11-02"},
        archive_default_days=21,
        display={"timezone": "Europe/London"},
    )
    saved = await client.put("/api/settings", json=body, headers=ME)
    assert saved.status_code == 200, saved.text
    result = saved.json()
    assert result["version"] == 2
    assert result["updated_at"] is not None
    expected = {k: v for k, v in body.items() if k != "version"}
    assert {k: v for k, v in result.items() if k not in ("version", "updated_at")} == expected
    again = (await client.get("/api/settings", headers=ME)).json()
    assert again == result


async def test_a_stale_write_is_a_409(client: httpx.AsyncClient) -> None:
    current = (await client.get("/api/settings", headers=ME)).json()
    first = await client.put("/api/settings", json=_update(current), headers=ME)
    assert first.status_code == 200
    stale = await client.put(
        "/api/settings", json=_update(current, archive_default_days=30), headers=ME
    )
    assert stale.status_code == 409
    assert stale.json()["current_version"] == 2
    assert (await client.get("/api/settings", headers=ME)).json()["archive_default_days"] == 14


async def test_only_a_maintainer_sets_the_llm_budget(client: httpx.AsyncClient) -> None:
    mine = (await client.get("/api/settings", headers=ME)).json()
    saved = await client.put(
        "/api/settings", json=_update(mine, llm={"monthly_budget_aud": 25.5}), headers=ME
    )
    assert saved.json()["llm"] == {"monthly_budget_aud": 25.5}

    theirs = (await client.get("/api/settings", headers=THEM)).json()
    ignored = await client.put(
        "/api/settings",
        json=_update(theirs, llm={"monthly_budget_aud": 9000}, archive_default_days=7),
        headers=THEM,
    )
    assert ignored.status_code == 200
    assert ignored.json()["llm"] == {"monthly_budget_aud": None}
    assert ignored.json()["archive_default_days"] == 7
    reread = (await client.get("/api/settings", headers=THEM)).json()
    assert reread["llm"] == {"monthly_budget_aud": None}
    # Each user's settings are their own.
    assert (await client.get("/api/settings", headers=ME)).json()["llm"] == {
        "monthly_budget_aud": 25.5
    }


async def test_inconsistent_or_invalid_settings_are_422(client: httpx.AsyncClient) -> None:
    current = (await client.get("/api/settings", headers=ME)).json()
    scoring = {**current["scoring"], "borderline_score": 70}
    bad = await client.put("/api/settings", json=_update(current, scoring=scoring), headers=ME)
    assert bad.status_code == 422
    pointers = {e["pointer"] for e in bad.json()["errors"]}
    assert "/scoring/borderline_score" in pointers
    late = {"after_hours": 72, "due_hours": 24, "nudge_days": 7}
    zone = {"timezone": "not a zone"}
    worse = await client.put(
        "/api/settings", json=_update(current, followups=late, display=zone), headers=ME
    )
    assert worse.status_code == 422
    assert {e["pointer"] for e in worse.json()["errors"]} >= {
        "/followups/due_hours",
        "/display/timezone",
    }
    schema = await client.put(
        "/api/settings", json=_update(current, archive_default_days=0), headers=ME
    )
    assert schema.status_code in (400, 422)
    assert (await client.get("/api/settings", headers=ME)).json()["version"] == 1
