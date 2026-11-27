"""`uv run python -m app.coordinator`: serve the coordinator with uvicorn.

Binds `COORDINATOR_HOST:COORDINATOR_PORT` (default 127.0.0.1:8100). Settings are read once here.
"""

from __future__ import annotations

import uvicorn

from app.coordinator.config import CoordinatorSettings
from app.coordinator.server import create_app


def main() -> None:
    settings = CoordinatorSettings()
    uvicorn.run(
        create_app(settings),
        host=settings.coordinator_host,
        port=settings.coordinator_port,
        log_level=settings.log_level.lower(),
        proxy_headers=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
