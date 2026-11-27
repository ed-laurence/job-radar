"""pytest plugin: write one JSON outcome per test to $SANDBOX_PYTEST_REPORT.

Loaded by the sandbox entrypoint with ``-p pytest_report`` (PYTHONPATH=/opt/sandbox). Only test
ids and outcomes are recorded; never output, tracebacks or captured logs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

_outcomes: dict[str, str] = {}


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    previous = _outcomes.get(report.nodeid)
    if report.failed:
        _outcomes[report.nodeid] = "failed"
    elif report.skipped and previous != "failed":
        _outcomes[report.nodeid] = "skipped"
    elif report.when == "call" and previous is None:
        _outcomes[report.nodeid] = "passed"


def pytest_sessionfinish() -> None:
    target = os.environ.get("SANDBOX_PYTEST_REPORT")
    if not target:
        return
    tests = [{"id": nodeid, "outcome": outcome} for nodeid, outcome in _outcomes.items()]
    Path(target).write_text(json.dumps({"tests": tests}), encoding="utf-8")
