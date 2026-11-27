"""Child processes for the agent adapters (docker, git), stdlib only.

Uses asyncio subprocesses. Under a loop without subprocess support (uvicorn `--reload` on
Windows runs a SelectorEventLoop), the same coroutine runs on a private loop in a worker thread,
so the stop signal is a `threading.Event` rather than an asyncio one. Output is discarded or
returned to the caller; nothing here logs argv, env or output.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

KILL_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class Completed:
    code: int
    stdout: bytes


class ProcessRunner(Protocol):
    async def run(
        self,
        argv: Sequence[str],
        env: Mapping[str, str],
        *,
        stop: threading.Event,
        kill_argv: Sequence[str],
        output: Path | None = None,
    ) -> int:
        """Run `argv` to exit and return its code. Once `stop` is set, run `kill_argv` (e.g.
        `docker kill <name>`), give the process a grace period, then kill it. With `output`,
        the process's stdout and stderr go to that file (created or truncated); otherwise
        they are discarded."""
        ...


async def _portable[T](work: Callable[[], Awaitable[T]]) -> T:
    try:
        return await work()
    except NotImplementedError:
        return await asyncio.to_thread(lambda: asyncio.run(_as_coroutine(work)))


async def _as_coroutine[T](work: Callable[[], Awaitable[T]]) -> T:
    return await work()


async def capture(
    argv: Sequence[str], *, env: Mapping[str, str], cwd: Path | None, timeout_s: float
) -> Completed:
    """Run to completion and return the exit code and stdout (stderr is discarded).
    Raises `TimeoutError` after killing the process."""

    async def work() -> Completed:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=dict(env),
            cwd=cwd,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
        except (TimeoutError, asyncio.CancelledError):
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise
        return Completed(code=proc.returncode or 0, stdout=stdout)

    return await _portable(work)


class AsyncioProcessRunner:
    def __init__(self, *, poll_s: float = 1.0, grace_s: float = 10.0) -> None:
        self._poll_s = poll_s
        self._grace_s = grace_s

    async def run(
        self,
        argv: Sequence[str],
        env: Mapping[str, str],
        *,
        stop: threading.Event,
        kill_argv: Sequence[str],
        output: Path | None = None,
    ) -> int:
        if output is None:
            return await _portable(lambda: self._supervise(argv, env, stop, kill_argv, None))
        with output.open("wb") as sink:
            return await _portable(lambda: self._supervise(argv, env, stop, kill_argv, sink))

    async def _supervise(
        self,
        argv: Sequence[str],
        env: Mapping[str, str],
        stop: threading.Event,
        kill_argv: Sequence[str],
        sink: BinaryIO | None,
    ) -> int:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL if sink is None else sink,
            stderr=asyncio.subprocess.DEVNULL if sink is None else asyncio.subprocess.STDOUT,
            env=dict(env),
        )
        try:
            while not stop.is_set():
                with contextlib.suppress(TimeoutError):
                    return await asyncio.wait_for(proc.wait(), self._poll_s)
            await self._stop(proc, env, kill_argv)
        except asyncio.CancelledError:
            await asyncio.shield(self._stop(proc, env, kill_argv))
            raise
        return await proc.wait()

    async def _stop(
        self, proc: asyncio.subprocess.Process, env: Mapping[str, str], kill_argv: Sequence[str]
    ) -> None:
        if proc.returncode is not None:
            return
        with contextlib.suppress(OSError, TimeoutError):
            await capture(kill_argv, env=env, cwd=None, timeout_s=KILL_TIMEOUT_S)
        try:
            await asyncio.wait_for(proc.wait(), self._grace_s)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
