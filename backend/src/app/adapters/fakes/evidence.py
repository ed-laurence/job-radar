"""In-memory `EvidenceStore`: the latest evidence test per (user, role)."""

from __future__ import annotations

import uuid

from app.domain.jobs.evidence_test import EvidenceTestRecord, record_uses


class MemoryEvidence:
    def __init__(self) -> None:
        self.tests: dict[tuple[uuid.UUID, uuid.UUID], EvidenceTestRecord] = {}

    async def latest(self, user_id: uuid.UUID, role_id: uuid.UUID) -> EvidenceTestRecord | None:
        return self.tests.get((user_id, role_id))

    async def save(self, user_id: uuid.UUID, record: EvidenceTestRecord) -> None:
        self.tests[(user_id, record.role_id)] = record

    async def record_uses(self, user_id: uuid.UUID) -> dict[uuid.UUID, int]:
        tests = [
            [item.record_id for item in test.items if item.match == "shown" and item.record_id]
            for (owner, _), test in self.tests.items()
            if owner == user_id
        ]
        return record_uses(tests)
