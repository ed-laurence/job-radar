"""The one step-stream mechanism for every agent run: dev-board runs and research runs.

Both store their steps in `run_steps`, whose inserts notify the shared `run_steps` channel
(ARCH-31). Notifications carry ids only and only wake the stream; it re-reads from its last
`seq`, so a dropped notification costs at most one poll interval.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from app.domain.ports import Notification, NotificationHub

RUN_STEPS_CHANNEL = "run_steps"


class Numbered(Protocol):
    @property
    def seq(self) -> int: ...


@dataclass(frozen=True)
class StepSource[S: Numbered, E]:
    """How to read one run: steps after a `seq` (at most `batch`), and its end marker once the
    run is finished (None while it is still going)."""

    run_id: uuid.UUID
    read_after: Callable[[int], Awaitable[Sequence[S]]]
    ended: Callable[[], Awaitable[E | None]]
    batch: int | None = None


@dataclass(frozen=True)
class Follow:
    poll_seconds: float
    # Give up waiting after this many idle polls (None: until the run ends).
    max_waits: int | None = None


async def follow_steps[S: Numbered, E](
    hub: NotificationHub | None,
    source: StepSource[S, E],
    after_seq: int,
    follow: Follow,
    on_give_up: Callable[[], Awaitable[E]] | None = None,
) -> AsyncIterator[S | E]:
    """Saved steps after `after_seq`, then live ones, then the run's end marker."""
    wake = asyncio.Event()
    async with _subscription(hub) as notes:
        pump = asyncio.get_running_loop().create_task(_pump(notes, source.run_id, wake))
        try:
            last, waits = after_seq, 0
            while True:
                # Status first: a step written just before the run ended is still read below.
                end = await source.ended()
                steps = await source.read_after(last)
                for step in steps:
                    last = step.seq
                    yield step
                if source.batch is not None and len(steps) >= source.batch:
                    continue
                if end is not None:
                    yield end
                    return
                if follow.max_waits is not None and waits >= follow.max_waits:
                    if on_give_up is not None:
                        yield await on_give_up()
                    return
                waits += 1
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), follow.poll_seconds)
                wake.clear()
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump


@contextlib.asynccontextmanager
async def _subscription(
    hub: NotificationHub | None,
) -> AsyncGenerator[AsyncIterator[Notification]]:
    if hub is None:
        yield _silence()
        return
    async with hub.subscribe(RUN_STEPS_CHANNEL) as notes:
        yield notes


async def _silence() -> AsyncIterator[Notification]:
    """No hub: nothing ever wakes the stream early, so it polls."""
    await asyncio.Event().wait()
    yield Notification(channel=RUN_STEPS_CHANNEL, payload="")


async def _pump(notes: AsyncIterator[Notification], run_id: uuid.UUID, wake: asyncio.Event) -> None:
    wanted = str(run_id)
    async for note in notes:
        if run_of(note) == wanted:
            wake.set()


def run_of(note: Notification) -> str | None:
    """The run id a `run_steps` notification is about (dev-board or research run)."""
    try:
        payload: object = json.loads(note.payload)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    value = cast("dict[str, object]", payload).get("run_id")
    return value if isinstance(value, str) else None
