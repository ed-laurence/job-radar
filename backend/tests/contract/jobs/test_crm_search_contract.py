"""`q` on listContacts and listTargets: routed as specified, a case-insensitive substring
search that combines with the other filters, at most 100 characters, per user."""

from __future__ import annotations

import httpx
from tests.conftest import MAINTAINER, USER, auth

from app.generated.models import ContactPage, TargetPage

ME = auth(MAINTAINER)
THEM = auth(USER)


async def _contact(client: httpx.AsyncClient, n: int, **fields: str) -> None:
    body = {"url": f"https://people.example.test/search-{n}", **fields}
    created = await client.post("/api/contacts", headers=ME, json=body)
    assert created.status_code == 201, created.text


async def test_contacts_q(client: httpx.AsyncClient) -> None:
    await _contact(client, 1, first_name="Riley", last_name="Tanaka", company="Northwind")
    await _contact(client, 2, first_name="Sam", last_name="Okafor", company="Northwind")
    found = ContactPage.model_validate(
        (await client.get("/api/contacts", headers=ME, params={"q": "RILEY tan"})).json()
    )
    assert [c.first_name for c in found.items] == ["Riley"]
    both = await client.get("/api/contacts", headers=ME, params={"q": " ", "company": "northwind"})
    assert len(ContactPage.model_validate(both.json()).items) == 2
    theirs = await client.get("/api/contacts", headers=THEM, params={"q": "riley"})
    assert ContactPage.model_validate(theirs.json()).items == []
    long = await client.get("/api/contacts", headers=ME, params={"q": "x" * 101})
    assert long.status_code == 422


async def test_targets_q(client: httpx.AsyncClient) -> None:
    for name, notes in (("Tailspin Toys", "Met at a meetup"), ("Contoso", "Cold lead")):
        body = {"name": name, "lane": "A", "notes": notes}
        assert (await client.post("/api/targets", headers=ME, json=body)).status_code == 201
    found = await client.get("/api/targets", headers=ME, params={"q": "MEETUP"})
    assert [t.name for t in TargetPage.model_validate(found.json()).items] == ["Tailspin Toys"]
    assert (
        await client.get("/api/targets", headers=ME, params={"q": "y" * 101})
    ).status_code == 422
