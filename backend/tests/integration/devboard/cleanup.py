"""Integration tests share one database per session: remove the rows a board test added, so
tests that count rows (the seed loader's) don't depend on test order."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import cast

import asyncpg
import pytest


async def _column(owner: asyncpg.Connection, query: str) -> list[object]:
    rows = cast("list[asyncpg.Record]", await owner.fetch(query))
    return [row[0] for row in rows]


@pytest.fixture(autouse=True)
async def leave_no_rows(owner: asyncpg.Connection) -> AsyncGenerator[None]:
    tasks = await _column(owner, "SELECT id FROM dev_tasks")
    roles = await _column(owner, "SELECT id FROM roles")
    state = await _column(owner, "SELECT key FROM app_state")
    yield
    # Deleting a card cascades to its events, triage, plans, runs, steps and leases.
    await owner.execute("DELETE FROM dev_tasks WHERE NOT (id = ANY($1::uuid[]))", tasks)
    await owner.execute("DELETE FROM roles WHERE NOT (id = ANY($1::uuid[]))", roles)
    await owner.execute("DELETE FROM app_state WHERE NOT (key = ANY($1::text[]))", state)
