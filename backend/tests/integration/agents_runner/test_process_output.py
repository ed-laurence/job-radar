"""The real process runner's `output` file: stdout and stderr of the child, in order, with the
Python interpreter standing in for docker."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

from app.adapters.agents.process import AsyncioProcessRunner

pytestmark = pytest.mark.integration


async def test_output_captures_stdout_and_stderr(tmp_path: Path) -> None:
    runner = AsyncioProcessRunner(poll_s=0.05, grace_s=0.5)
    log = tmp_path / "runner.log"
    script = (
        "import sys; print('to stdout', flush=True); "
        "print('to stderr', file=sys.stderr, flush=True); raise SystemExit(4)"
    )
    argv = [sys.executable, "-c", script]
    code = await runner.run(
        argv, dict(os.environ), stop=threading.Event(), kill_argv=["x"], output=log
    )
    assert code == 4
    text = log.read_text(encoding="utf-8")
    assert "to stdout" in text
    assert "to stderr" in text
    assert text.index("to stdout") < text.index("to stderr")
