"""Path leases (PLAN "Collision rules"): exclusive leases on concrete paths or directory
prefixes, with a TTL and heartbeat, plus single-writer hotspot locks.

A path inside a hotspot (`web/src/lib/x.ts`) is leased as the whole hotspot (`web/src/lib/`),
so two writers can never edit the same hotspot at once even on different files.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from app.domain.devboard.model import Actor, LeaseKind, LeaseRecord
from app.domain.devboard.paths import (
    InvalidPath,
    contains,
    hotspot_for,
    is_writable,
    normalise,
)
from app.domain.errors import FieldError, ValidationFailed
from app.domain.ports import Clock
from app.domain.ports.devboard import LeaseClaim, LeaseOutcome, LeaseStore

MIN_TTL = timedelta(seconds=30)
MAX_TTL = timedelta(hours=1)
DEFAULT_TTL = timedelta(minutes=10)
MAX_PATHS = 50


def lease_targets(raw_paths: tuple[str, ...]) -> tuple[tuple[str, LeaseKind], ...]:
    """Normalised, de-duplicated `(path, kind)` pairs. Raises `ValidationFailed`."""
    if not raw_paths or len(raw_paths) > MAX_PATHS:
        raise ValidationFailed(f"Claim between 1 and {MAX_PATHS} paths.")
    targets: dict[str, LeaseKind] = {}
    errors: list[FieldError] = []
    for index, raw in enumerate(raw_paths):
        try:
            path = normalise(raw)
        except InvalidPath as error:
            errors.append(FieldError(pointer=f"/paths/{index}", message=str(error)))
            continue
        spot = hotspot_for(path)
        if spot is None and not is_writable(path):
            errors.append(FieldError(pointer=f"/paths/{index}", message="outside the write scope"))
            continue
        if spot is not None and contains(spot, path):
            targets[spot] = LeaseKind.HOTSPOT
        elif spot is not None:
            targets[path] = LeaseKind.HOTSPOT
        else:
            targets.setdefault(path, LeaseKind.EXCLUSIVE)
    if errors:
        raise ValidationFailed("Some lease paths can't be claimed.", errors=errors)
    return tuple(sorted(targets.items()))


def _ttl(ttl: timedelta) -> timedelta:
    if not MIN_TTL <= ttl <= MAX_TTL:
        raise ValidationFailed("Lease TTL must be between 30 seconds and 1 hour.")
    return ttl


class LeaseService:
    def __init__(self, store: LeaseStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def claim(
        self,
        actor: Actor,
        run_id: uuid.UUID,
        task_id: uuid.UUID,
        paths: tuple[str, ...],
        ttl: timedelta = DEFAULT_TTL,
    ) -> LeaseOutcome:
        now = self._clock.now()
        claim = LeaseClaim(
            run_id=run_id,
            task_id=task_id,
            paths=lease_targets(paths),
            at=now,
            expires_at=now + _ttl(ttl),
        )
        return await self._store.claim(actor.user_id, claim)

    async def heartbeat(self, actor: Actor, run_id: uuid.UUID, ttl: timedelta = DEFAULT_TTL) -> int:
        now = self._clock.now()
        return await self._store.renew(actor.user_id, run_id, now, now + _ttl(ttl))

    async def release_run(self, actor: Actor, run_id: uuid.UUID) -> int:
        return await self._store.release(actor.user_id, self._clock.now(), run_id=run_id)

    async def release_task(self, actor: Actor, task_id: uuid.UUID) -> int:
        return await self._store.release(actor.user_id, self._clock.now(), task_id=task_id)

    async def reclaim_expired(self, actor: Actor) -> int:
        return await self._store.reclaim_expired(actor.user_id, self._clock.now())

    async def active(self, actor: Actor) -> list[LeaseRecord]:
        return await self._store.active(actor.user_id, self._clock.now())
