"""Write /out/handoff.json in the AGENTS.md handoff shape (stdlib only).

Reads the JSON test reports produced inside the sandbox (pytest via ``pytest_report.py``,
vitest via ``--reporter=json``) and the changed-path list against the base commit. It never
copies test output or logs into the handoff: only counts, the first failing test id and paths.

With ``--agent-result`` (written by agent_runner.py) the agent's own ``post_handoff`` supplies
the summary, rubric IDs and open questions. Test counts and changed paths always come from the
sandbox's re-run and the git diff, never from the agent's claims. When the run stopped early
the summary leads with the runner's plain-words ``detail`` ("Stopped: model budget used up
after 4 turns ($1.97 of $2.00)."). ``--tests-not-run`` says the agent changed nothing, so the
sandbox skipped the tests.

``--plan-mode --plan-json /out/plan.json`` (AGENT_MODE=plan): no tests and no diff; the
status is ``done`` exactly when plan.json exists and meets the contract (``plan_contract``),
else ``failed`` with the runner's ``detail``.

``--triage-mode --triage-json /out/triage.json`` (AGENT_MODE=triage): the same, against the
triage contract (``triage_contract``); the summary is built from its enums and counts only.

``--review-mode --review-json /out/review.json --changed-files F`` (AGENT_MODE=review): the
same, against the review contract (``review_contract``, every path one of the changed files);
the summary is built from the finding counts only.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import plan_contract
import review_contract
import triage_contract

MAX_SUMMARY_WORDS = 80
MAX_SKIPPED = 10
MAX_ITEM_CHARS = 160
MAX_RUBRIC = 30
MAX_QUESTIONS = 3
MAX_DETAIL_CHARS = 300
STATUSES = ("done", "failed", "error")
KILLED_DETAIL = (
    "Stopped: wall-clock limit reached; the runner was killed before it wrote its result."
)


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
    detail: str | None = None

    @property
    def finished(self) -> bool:
        return self.stop == "end_turn" and self.agent_status in (None, "done")


def read_agent_result(path: Path | None) -> AgentOutcome:
    """A missing or unreadable result means the agent was killed (wall-clock timeout)."""
    if path is None or not path.is_file():
        return AgentOutcome(stop="timeout", agent_status="missing", detail=KILLED_DETAIL)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AgentOutcome(stop="error")
    data = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    stop = data.get("stop")
    stop = stop if isinstance(stop, str) and stop else "error"
    raw_detail = data.get("detail")
    detail = raw_detail.strip()[:MAX_DETAIL_CHARS] if isinstance(raw_detail, str) else None
    posted = data.get("handoff")
    handoff = cast("dict[str, object]", posted) if isinstance(posted, dict) else {}
    if not handoff:
        return AgentOutcome(stop=stop, agent_status="missing", detail=detail or None)
    summary = handoff.get("summary")
    status = handoff.get("status")
    return AgentOutcome(
        stop=stop,
        summary=summary if isinstance(summary, str) and summary.strip() else None,
        agent_status=status if isinstance(status, str) else "missing",
        rubric_checked=_strings(handoff.get("rubric_checked"), MAX_RUBRIC),
        open_questions=_strings(handoff.get("open_questions"), MAX_QUESTIONS),
        detail=detail or None,
    )


def build(
    task_id: str,
    counts: Counts,
    paths: list[str],
    skipped: list[str],
    status: str | None = None,
    summary: str | None = None,
    agent: AgentOutcome | None = None,
    tests_ran: bool = True,
) -> dict[str, object]:
    tally = f"{counts.passed} passed, {counts.failed} failed, {counts.skipped} skipped"
    tests = f"Sandbox re-run: {tally}." if tests_ran else "No changes; tests not run."
    if status is None:
        unfinished = agent is not None and not agent.finished
        status = "failed" if counts.failed or unfinished else "done"
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    if summary is None and agent is not None:
        summary = agent.summary
        if not agent.finished and agent.detail:
            summary = f"{agent.detail} {summary or ''} {tests}"
            summary = " ".join(summary.split())
        elif summary is None or not agent.finished:
            reason = (
                "without post_handoff"
                if agent.agent_status == "missing"
                else f"with handoff status {agent.agent_status}"
            )
            lead = f"Agent run for task {task_id} stopped ({agent.stop}) {reason}"
            summary = f"{lead}. {summary or ''} {tests}".replace("  ", " ")
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


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


def read_plan(task_id: str, path: Path) -> dict[str, object] | None:
    """The plan run's ``/out/plan.json`` if it exists and meets the contract, else None."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return plan_contract.validate(raw, task_id)
    except (OSError, ValueError, plan_contract.PlanError):
        return None


def build_plan(
    task_id: str, plan_path: Path, agent: AgentOutcome, skipped: list[str]
) -> dict[str, object]:
    """A plan run's handoff: ``done`` exactly when a valid plan.json was written."""
    plan = read_plan(task_id, plan_path)
    if plan is None:
        status = "failed"
        lead = agent.detail or f"Plan run for task {task_id} stopped ({agent.stop})"
        summary = f"{lead} No valid plan was written."
    else:
        status = "done"
        tests = len(plan_contract.acceptance_tests(plan))
        to_change = len(cast("list[object]", plan["tests_to_change"]))
        summary = (
            f"Plan for task {task_id}: "
            f"{_count(len(plan_contract.as_dicts(plan['subtasks'])), 'subtask')} "
            f"({plan['mode']}), {_count(tests, 'acceptance test')}, "
            f"{_count(to_change, 'existing test')} to change; budget "
            f"${float(cast('float', plan['budget_usd'])):.2f}, {plan['budget_minutes']} min, "
            f"risk {plan['risk']}. {agent.detail or ''}"
        )
    out = build(task_id, Counts(), [], ["plan run: no tests run", *skipped], status, summary, None)
    return out


def read_triage(task_id: str, path: Path) -> dict[str, object] | None:
    """The triage run's ``/out/triage.json`` if it exists and meets the contract, else None."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return triage_contract.validate(raw, task_id)
    except (OSError, ValueError, triage_contract.TriageError):
        return None


def build_triage(
    task_id: str, triage_path: Path, agent: AgentOutcome, skipped: list[str]
) -> dict[str, object]:
    """A triage run's handoff: ``done`` exactly when a valid triage.json was written."""
    triage = read_triage(task_id, triage_path)
    if triage is None:
        status = "failed"
        lead = agent.detail or f"Triage run for task {task_id} stopped ({agent.stop})"
        summary = f"{lead} No valid triage was written."
    else:
        status = "done"
        summary = (
            f"Triage for task {task_id}: {triage_contract.summary(triage)}. {agent.detail or ''}"
        )
    skips = ["triage run: no suite run", *skipped]
    return build(task_id, Counts(), [], skips, status, summary, None)


def read_review(task_id: str, path: Path, changed: frozenset[str]) -> dict[str, object] | None:
    """The review run's ``/out/review.json`` if it exists and meets the contract, else None."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return review_contract.validate(raw, task_id, changed)
    except (OSError, ValueError, review_contract.ReviewError):
        return None


def build_review(
    task_id: str,
    review_path: Path,
    changed: frozenset[str],
    agent: AgentOutcome,
    skipped: list[str],
) -> dict[str, object]:
    """A review run's handoff: ``done`` exactly when a valid review.json was written."""
    review = read_review(task_id, review_path, changed)
    if review is None:
        status = "failed"
        lead = agent.detail or f"Review run for task {task_id} stopped ({agent.stop})"
        summary = f"{lead} No valid review was written."
    else:
        status = "done"
        summary = (
            f"Review for task {task_id}: {review_contract.summary(review)}. {agent.detail or ''}"
        )
    skips = ["review run: no tests run", *skipped]
    return build(task_id, Counts(), [], skips, status, summary, None)


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
    parser.add_argument(
        "--tests-not-run", action="store_true", help="the agent changed nothing; no tests ran"
    )
    parser.add_argument("--plan-mode", action="store_true", help="a plan run (no tests, no diff)")
    parser.add_argument("--plan-json", type=Path, help="plan mode: the plan run's plan.json")
    parser.add_argument("--triage-mode", action="store_true", help="a triage run (no diff)")
    parser.add_argument("--triage-json", type=Path, help="triage mode: the run's triage.json")
    parser.add_argument("--review-mode", action="store_true", help="a review run (no diff)")
    parser.add_argument("--review-json", type=Path, help="review mode: the run's review.json")
    parser.add_argument(
        "--changed-files", type=Path, help="review mode: git diff --name-only -z base..HEAD"
    )
    args = parser.parse_args(argv)

    if args.review_mode:
        if args.review_json is None or args.changed_files is None:
            parser.error("--review-mode needs --review-json and --changed-files")
        agent = read_agent_result(args.agent_result)
        changed = review_contract.read_changed(args.changed_files)
        review = build_review(args.task_id, args.review_json, changed, agent, args.skipped)
        _write(args.out, review)
        return 0

    if args.triage_mode:
        if args.triage_json is None:
            parser.error("--triage-mode needs --triage-json")
        agent = read_agent_result(args.agent_result)
        _write(args.out, build_triage(args.task_id, args.triage_json, agent, args.skipped))
        return 0

    if args.plan_mode:
        if args.plan_json is None:
            parser.error("--plan-mode needs --plan-json")
        agent = read_agent_result(args.agent_result)
        handoff = build_plan(args.task_id, args.plan_json, agent, args.skipped)
        _write(args.out, handoff)
        return 0

    counts = Counts()
    for path in args.pytest_json:
        read_pytest(path, counts)
    for path in args.vitest_json:
        read_vitest(path, counts)
    paths = read_changed(args.changed) if args.changed else []
    agent = read_agent_result(args.agent_result) if args.agent_mode else None
    handoff = build(
        args.task_id,
        counts,
        paths,
        args.skipped,
        args.status,
        args.summary,
        agent,
        tests_ran=not args.tests_not_run,
    )
    _write(args.out, handoff)
    return 0


def _write(out: Path, handoff: dict[str, object]) -> None:
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(handoff, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)


if __name__ == "__main__":
    sys.exit(main())
