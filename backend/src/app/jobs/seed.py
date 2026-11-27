"""Seed loader: `python -m app.jobs.seed` loads `seed/*.jsonl` (see `seed/manifest.json`).

- Idempotent: every row gets a deterministic id (uuid5 of user, table and seed id) and inserts
  skip rows that already exist, so re-running changes nothing.
- Dates are re-based by `now - manifest.anchor`, keeping their relative spacing.
- `owner@example.test` maps to the maintainer in `APP_USERS` and `tester@example.test` to the
  user account; without `APP_USERS` the seed accounts themselves are created.
- Job-search rows (roles, the scan pool, records, contacts, spend...) belong to the owner
  (maintainer) only, under the owner's RLS scope: 300 visible roles and the rest in
  `role_pool`. The tester account is provisioned with an empty tenant, so scans release the
  owner's pool once rather than a second copy. The seed has no dev-board data: the board
  starts empty.
- The owner's "Your extra skills" come from `seed/owner-settings.json`, set only while empty
  (`app.jobs.seed_settings`).
- The owner also gets the demo CVs (`seed/documents/`) as profile documents, uploaded through
  the same service as a user's upload (`app.jobs.seed_documents`); a filled slot is skipped.

Runs with the database owner's `DATABASE_URL` (it provisions `users`, which the app role can't)."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.adapters.db import tables_platform as platform
from app.adapters.db.engine import (
    session_factory,
    system_transaction,
    user_transaction,
)
from app.adapters.system import SystemClock
from app.compose.jobs import document_service, job_stores
from app.domain.identity import AppRole, Principal
from app.domain.jobs.documents import ProfileDocumentService
from app.domain.ports import Clock
from app.jobs.seed_context import (
    CHUNK,
    Row,
    SeedContext,
    accounts,
    read_seed,
    table_of,
)
from app.jobs.seed_documents import seed_documents
from app.jobs.seed_jobs import job_rows
from app.jobs.seed_settings import owner_skills, seed_skills
from app.main import build_container
from app.settings import Settings

# Only the owner gets the demo data; every other seeded account starts empty.
DEMO_DATA_ROLE = AppRole.MAINTAINER


async def _insert(session: AsyncSession, table: Table, rows: Sequence[Row]) -> int:
    written = 0
    for start in range(0, len(rows), CHUNK):
        # A multi-row VALUES needs the same keys in every row; absent optional keys are NULL.
        names = list(dict.fromkeys(name for row in rows for name in row))
        chunk = [{name: row.get(name) for name in names} for row in rows[start : start + CHUNK]]
        result = await session.execute(insert(table).values(chunk).on_conflict_do_nothing())
        written += max(0, int(getattr(result, "rowcount", 0) or 0))
    return written


async def _load_tables(
    sessions: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    batches: Iterable[tuple[Table, list[Row]]],
) -> int:
    written = 0
    async with user_transaction(sessions, user_id) as session:
        for table, rows in batches:
            if rows:
                written += await _insert(session, table, rows)
    return written


async def load_seed(
    settings: Settings,
    engine: AsyncEngine,
    clock: Clock,
    documents: ProfileDocumentService | None = None,
) -> dict[str, int]:
    """Load everything once. Returns the rows (demo CVs and extra skills included) written per
    account email (0 on a re-run). Without `documents` the demo CVs are left out."""
    anchor, data = read_seed(settings.seed_dir)
    shift = clock.now() - anchor
    sessions = session_factory(engine)
    found = accounts(settings, data["users"])
    skills = owner_skills(settings.seed_dir)
    async with system_transaction(sessions) as session:
        await _insert(
            session,
            table_of(platform.User),
            [
                {
                    "id": a.user_id,
                    "email": a.email.lower(),
                    "role": a.role.value,
                    "display_name": a.display_name,
                }
                for a in found
            ],
        )
    report: dict[str, int] = {}
    for account in found:
        if account.role is not DEMO_DATA_ROLE:
            report[account.email] = 0  # provisioned, but an empty tenant
            continue
        ctx = SeedContext(data, shift, account, anchor + shift)
        report[account.email] = await _load_tables(sessions, account.user_id, job_rows(ctx))
        if skills is not None:
            report[account.email] += await seed_skills(sessions, account.user_id, skills)
        if documents is not None:
            principal = Principal(account.user_id, account.email, account.role)
            folder = settings.seed_dir / "documents"
            report[account.email] += len(await seed_documents(documents, principal, folder))
    return report


def main() -> None:
    settings = Settings()
    if settings.database_url is None:
        raise SystemExit("DATABASE_URL is required to load the seed.")
    container = build_container(settings)
    engine = container.engine
    if engine is None:
        raise SystemExit("DATABASE_URL is required to load the seed.")
    documents = document_service(container, job_stores(container))

    async def run() -> dict[str, int]:
        try:
            return await load_seed(settings, engine, SystemClock(), documents)
        finally:
            await container.aclose()

    report = asyncio.run(run())
    sys.stdout.write(json.dumps({"seeded_rows": sum(report.values())}) + "\n")


if __name__ == "__main__":
    main()
