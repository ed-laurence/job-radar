"""Write /out/handoff.json in the AGENTS.md handoff shape (stdlib only).

Reads the JSON test reports produced inside the sandbox (pytest via ``pytest_report.py``,
vitest via ``--reporter=json``) and the changed-path list against the base commit. It never
copies test output or logs into the handoff: only counts, the first failing test id and paths.

With ``--agent-result`` (written by agent_runner.py) the agent's own ``post_handoff`` supplies
the summary, rubric IDs and open questions. Test counts and changed paths always come from the
sandbox's re-run and the git diff, never from the agent's claims.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

MAX_SUMMARY_WORDS = 80
MAX_SKIPPED = 10
MAX_ITEM_CHARS = 160
MAX_RUBRIC = 30
MAX_QUESTIONS = 3
STATUSES = ("done", "failed", "error")


@dataclass
class Counts:
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    first_failing: str | None = None


def _record(counts: Counts, test_id: str, outcome: str) -> None:
    if outcome == "failed":
        counts.failed += 1
        if counts.first_failing is None:
            counts.first_failing = test_id[:MAX_ITEM_CHARS]
    elif outcome == "passed":
        counts.passed += 1
    else:
        counts.skipped += 1


def read_pytest(path: Path, counts: Counts) -> None:
    """``{"tests": [{"id": nodeid, "outcome": "passed|failed|skipped"}]}``."""
    for test in json.loads(path.read_text(encoding="utf-8")).get("tests", []):
        _record(counts, str(test.get("id", "")), str(test.get("outcome", "")))


def read_vitest(path: Path, counts: Counts) -> None:
    """Vitest's Jest-compatible JSON reporter output."""
    for suite in json.loads(path.read_text(encoding="utf-8")).get("testResults", []):
        for test in suite.get("assertionResults", []):
            test_id = f"{suite.get('name', '')}::{test.get('fullName', '')}"
            _record(counts, test_id, str(test.get("status", "")))


def read_changed(path: Path) -> list[str]:
    """``git diff --name-only base..HEAD`` output, written by the entrypoint."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return sorted({line.strip() for line in lines if line.strip()})


def _cap_words(text: str, limit: int) -> str:
    words = text.split()
    return " ".join(words[:limit])


def _strings(value: object, limit: int) -> list[str]:
    items = cast("list[object]", value) if isinstance(value, list) else []
    return [s.strip()[:MAX_ITEM_CHARS] for s in items if isinstance(s, str) and s.strip()][:limit]


@dataclass
class AgentOutcome:
    """The parts of agent_runner's result that the handoff uses."""

    stop: str
    summary: str | None = None
    agent_status: str | None = None
    rubric_checked: list[str] = field(default_factory=list[str])
    open_questions: list[str] = field(default_factory=list[str])

    @property
    def finished(self) -> bool:
        return self.stop == "end_turn" and self.agent_status in (None, "done")


def read_agent_result(path: Path | None) -> AgentOutcome:
    """A missing or unreadable result means the agent was killed (wall-clock timeout)."""
    if path is None or not path.is_file():
        return AgentOutcome(stop="timeout")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AgentOutcome(stop="error")
    data = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    stop = data.get("stop")
    stop = stop if isinstance(stop, str) and stop else "error"
    posted = data.get("handoff")
    handoff = cast("dict[str, object]", posted) if isinstance(posted, dict) else {}
    if not handoff:
        return AgentOutcome(stop=stop, agent_status="missing")
    summary = handoff.get("summary")
    status = handoff.get("status")
    return AgentOutcome(
        stop=stop,
        summary=summary if isinstance(summary, str) and summary.strip() else None,
        agent_status=status if isinstance(status, str) else "missing",
        rubric_checked=_strings(handoff.get("rubric_checked"), MAX_RUBRIC),
        open_questions=_strings(handoff.get("open_questions"), MAX_QUESTIONS),
    )


def build(
    task_id: str,
    counts: Counts,
    paths: list[str],
    skipped: list[str],
    status: str | None = None,
    summary: str | None = None,
    agent: AgentOutcome | None = None,
) -> dict[str, object]:
    tally = f"{counts.passed} passed, {counts.failed} failed, {counts.skipped} skipped"
    if status is None:
        unfinished = agent is not None and not agent.finished
        status = "failed" if counts.failed or unfinished else "done"
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    if summary is None and agent is not None:
        summary = agent.summary
        if summary is None or not agent.finished:
            reason = (
                "without post_handoff"
                if agent.agent_status == "missing"
                else f"with handoff status {agent.agent_status}"
            )
            lead = f"Agent run for task {task_id} stopped ({agent.stop}) {reason}"
            summary = f"{lead}. {summary or ''} Sandbox re-run: {tally}.".replace("  ", " ")
    if summary is None:
        summary = (
            f"Stub agent for task {task_id}: no edits; ran the repo tests in the sandbox ({tally})."
        )
    return {
        "task_id": task_id,
        "status": status,
        "summary": _cap_words(summary, MAX_SUMMARY_WORDS),
        "changed_paths": paths,
        "tests": {
            "passed": counts.passed,
            "failed": counts.failed,
            "skipped": counts.skipped,
            "first_failing": counts.first_failing,
        },
        "rubric_checked": agent.rubric_checked if agent else [],
        "open_questions": agent.open_questions if agent else [],
        "skipped_checks": [s[:MAX_ITEM_CHARS] for s in skipped[:MAX_SKIPPED]] or ["none"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="handoff")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--changed", type=Path, help="file with one changed path per line")
    parser.add_argument("--pytest-json", action="append", default=[], type=Path)
    parser.add_argument("--vitest-json", action="append", default=[], type=Path)
    parser.add_argument("--skipped", action="append", default=[])
    parser.add_argument("--status", choices=STATUSES)
    parser.add_argument("--summary")
    parser.add_argument("--agent-result", type=Path, help="agent_runner.py result JSON")
    parser.add_argument("--agent-mode", action="store_true", help="the real agent ran")
    args = parser.parse_args(argv)

    counts = Counts()
    for path in args.pytest_json:
        read_pytest(path, counts)
    for path in args.vitest_json:
        read_vitest(path, counts)
    paths = read_changed(args.changed) if args.changed else []
    agent = read_agent_result(args.agent_result) if args.agent_mode else None
    handoff = build(args.task_id, counts, paths, args.skipped, args.status, args.summary, agent)
    tmp = args.out.with_suffix(".tmp")
    tmp.write_text(json.dumps(handoff, indent=2) + "\n", encoding="utf-8")
    tmp.replace(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
