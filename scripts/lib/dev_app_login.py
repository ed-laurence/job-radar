"""Create/refresh the local restricted API login and print its DATABASE_URL.

The API must never serve as a superuser or table owner, because Postgres skips row-level
security for those roles (SEC-03; the API refuses to start otherwise). Migrations and seeding run
as the admin URL; this login only holds the `jobradar_app` role created by migration 0001.

Usage (from backend/): uv run python ../scripts/lib/dev_app_login.py <admin DATABASE_URL>
"""

from __future__ import annotations

import asyncio
import secrets
import sys

import asyncpg
from sqlalchemy.engine import make_url

LOGIN = "jobradar_dev_app"


async def main(admin_url: str) -> str:
    password = secrets.token_urlsafe(24)
    plain = make_url(admin_url).set(drivername="postgresql").render_as_string(hide_password=False)
    connection = await asyncpg.connect(plain)
    try:
        exists = await connection.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", LOGIN)
        verb = "ALTER" if exists else "CREATE"
        # Role names and passwords can't be bind parameters in DDL; both are generated locally.
        await connection.execute(
            f"{verb} ROLE {LOGIN} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{password}'"
        )
        await connection.execute(f"GRANT jobradar_app TO {LOGIN}")
    finally:
        await connection.close()
    return (
        make_url(admin_url)
        .set(username=LOGIN, password=password)
        .render_as_string(hide_password=False)
    )


if __name__ == "__main__":
    print(asyncio.run(main(sys.argv[1])))
