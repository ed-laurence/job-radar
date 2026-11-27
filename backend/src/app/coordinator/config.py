"""Coordinator settings: the app `Settings` (ARCH-06) plus the coordinator's own env vars.

- `COORDINATOR_TOKEN_KEY`: HMAC key for task tokens (>= 32 bytes). Never logged.
- `COORDINATOR_HOST` / `COORDINATOR_PORT`: bind address (default 127.0.0.1:8100; the
  container sets 0.0.0.0, reachable only on the compose networks and host loopback).
- `COORDINATOR_ALLOWED_HOSTS`: JSON list of accepted `Host` names (DNS-rebinding protection;
  a `:port` suffix is ignored).
- `DATABASE_URL`: the restricted app login (RLS enforced; the process refuses a superuser).
"""

from __future__ import annotations

from pydantic import Field, SecretStr

from app.settings import Settings

DEFAULT_PORT = 8100
DB_POOL_SIZE = 2


class CoordinatorSettings(Settings):
    coordinator_token_key: SecretStr | None = None
    coordinator_host: str = "127.0.0.1"
    coordinator_port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    coordinator_allowed_hosts: tuple[str, ...] = (
        f"coordinator:{DEFAULT_PORT}",
        f"127.0.0.1:{DEFAULT_PORT}",
        f"localhost:{DEFAULT_PORT}",
    )

    def token_key(self) -> bytes:
        if self.coordinator_token_key is None:
            raise ValueError("COORDINATOR_TOKEN_KEY is not set")
        return self.coordinator_token_key.get_secret_value().encode("utf-8")
