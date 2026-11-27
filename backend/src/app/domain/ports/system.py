"""Clock, Ids and Storage ports (CODE-07, ARCH-02)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Timezone-aware UTC now."""
        ...


class Ids(Protocol):
    def new_id(self) -> uuid.UUID: ...

    def new_correlation_id(self) -> str: ...


class Storage(Protocol):
    """Object storage for uploads, PDFs and bundles (GCS in prod)."""

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes:
        """Raises `NotFound` when the key doesn't exist."""
        ...

    async def delete(self, key: str) -> None: ...
