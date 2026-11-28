"""The local PR sync poller (docs/features/github-prs.md, Flow step 6), run in-process from
the app lifespan when `CODE_HOST=github` and `PR_SYNC_INTERVAL_S` > 0.

A pass, then a wait of `interval_s`, until cancelled. A failed pass never ends the loop: each
distinct failure (a GitHub outage reason, or an unexpected error's type) is logged once, with no
error text, so a signed-out gh doesn't flood the log.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

import structlog

from app.domain.devboard.model import Actor
from app.domain.devboard.pr_sync import PrSyncSummary

log = structlog.get_logger("app.jobs.pr_sync")


class PrSync(Protocol):
    async def sync(self, actor: Actor) -> PrSyncSummary: ...


async def run_pr_sync(
    service: PrSync,
    interval_s: float,
    *,
    actor: Actor,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Runs until cancelled (`CancelledError` propagates)."""
    if interval_s <= 0:
        raise ValueError("The PR sync interval must be positive.")
    logged: set[str] = set()
    while True:
        try:
            summary = await service.sync(actor)
        except Exception as error:  # the poller must outlive any failed pass (never the app)
            kind = type(error).__name__
            if kind not in logged:
                logged.add(kind)
                log.exception("pr_sync_failed", error=kind)
        else:
            if summary.failure is not None and summary.failure not in logged:
                logged.add(summary.failure)
                log.warning("pr_sync_unavailable", reason=summary.failure)
        await sleep(interval_s)
