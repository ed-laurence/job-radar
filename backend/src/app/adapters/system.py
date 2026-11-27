"""Real Clock and Ids. The only place allowed to read wall-clock time or mint random ids."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidIds:
    def new_id(self) -> uuid.UUID:
        return uuid.uuid4()

    def new_correlation_id(self) -> str:
        return str(uuid.uuid4())
