"""Embedded Postgres 16 for local dev and integration tests (used by scripts/dev-db.sh).

Run through uv so nothing is installed into a project env:
    uv run --no-project --with pgserver==<pinned> python pgserver_ctl.py start|stop|status <pgdata> <dbname>

`start` prints the DATABASE_URL on stdout. The server keeps running after this process exits.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _url(uri: str, dbname: str) -> str:
    # pgserver returns ".../postgres" (TCP on Windows, a socket query string on POSIX).
    base, _, query = uri.partition("?")
    base = base.rsplit("/", 1)[0] + "/" + dbname
    base = base.replace("postgres:@", "postgres@")
    return f"{base}?{query}" if query else base


def start(pgdata: Path, dbname: str) -> int:
    import pgserver

    pgdata.parent.mkdir(parents=True, exist_ok=True)
    srv = pgserver.get_server(pgdata, cleanup_mode=None)
    rows = srv.psql(f"SELECT 1 AS present FROM pg_database WHERE datname = '{dbname}';")
    if "(1 row)" not in rows:
        srv.psql(f'CREATE DATABASE "{dbname}";')
    print(_url(srv.get_uri(), dbname))
    return 0


def stop(pgdata: Path) -> int:
    if not (pgdata / "postmaster.pid").exists():
        print("not running", file=sys.stderr)
        return 0
    from pgserver._commands import pg_ctl

    pg_ctl(["-w", "stop", "-m", "fast"], pgdata=pgdata)
    print("stopped", file=sys.stderr)
    return 0


def status(pgdata: Path) -> int:
    pid_file = pgdata / "postmaster.pid"
    if not pid_file.exists():
        print("not running", file=sys.stderr)
        return 1
    from pgserver._commands import pg_ctl

    try:
        pg_ctl(["status"], pgdata=pgdata)
    except Exception:
        print("stale postmaster.pid; not running", file=sys.stderr)
        return 1
    print("running", file=sys.stderr)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] not in {"start", "stop", "status"}:
        print(__doc__, file=sys.stderr)
        return 2
    pgdata = Path(argv[2]).expanduser().resolve()
    dbname = argv[3] if len(argv) > 3 else "jobradar"
    if not dbname.replace("_", "").isalnum():
        print("invalid database name", file=sys.stderr)
        return 2
    if argv[1] == "start":
        return start(pgdata, dbname)
    if argv[1] == "stop":
        return stop(pgdata)
    return status(pgdata)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
