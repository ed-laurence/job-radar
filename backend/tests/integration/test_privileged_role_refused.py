"""SEC-03 regression: the API must refuse to serve as a role that bypasses row-level security.

Found in local integration: dev ran the API as the `postgres` superuser, so RLS was silently
skipped and one user could read another's roles. Startup now fails closed.
"""

from __future__ import annotations

import pytest

from app.adapters.db.engine import PrivilegedDatabaseRole, assert_rls_enforced, create_engine
from tests.integration.conftest import Database

pytestmark = pytest.mark.integration


async def test_superuser_or_owner_role_is_refused(database: Database) -> None:
    engine = create_engine(database.owner_url, pool_size=1)
    try:
        with pytest.raises(PrivilegedDatabaseRole):
            await assert_rls_enforced(engine)
    finally:
        await engine.dispose()


async def test_app_login_granted_jobradar_app_is_accepted(database: Database) -> None:
    engine = create_engine(database.app_url, pool_size=1)
    try:
        await assert_rls_enforced(engine)
    finally:
        await engine.dispose()
