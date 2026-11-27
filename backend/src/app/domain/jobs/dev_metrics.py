"""Dev-board flow and agent metrics (spec `getDevMetrics`) for `[window.start, window.end)`.

Definitions (live = not deleted):
- done entry: a live card's move into `done`. A card counts once per ISO week it reached Done.
- throughput: cards with a done entry in the range, per ISO week (Monday), zero weeks included.
- WIP: live cards per column, for columns in flight (not `backlog`, `out_of_scope`, `done`).
- cycle time: median hours from entering a column to the next move, for moves in the range.
- lead time: hours from card creation to its first done entry in the range (p50, p90).
- agent pass rate: first-round review runs finished in the range that were approved.
- review rounds: per card with a review run finished in the range, highest round + 1; averaged.
- out of scope: the latest triage record per card made in the range; share not in scope.
- cost per task: total run cost of the cards counted in lead time, per card.
- run outcomes: runs created in the range by status (`timed_out` counts as `failed`).
- blocked: live cards in `blocked` now. Oldest human review: hours since the oldest live
  `human_review` card entered that column (its creation time when no entry is recorded).
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.domain.jobs.board_columns import NOT_IN_FLIGHT, TASK_COLUMNS, cycle_samples
from app.domain.jobs.metrics import DateRange, bucket_start, hours_between, percentile, rounded
from app.domain.jobs.ports import ColumnEntry, DevBoardFacts, RunFact, TaskFact

OUTCOMES = ("succeeded", "failed", "killed", "running", "queued")
OUTCOME_OF_STATUS = {"timed_out": "failed"}
MAX_WEEKS = 60


@dataclass(frozen=True, kw_only=True)
class DevMetricsView:
    throughput: tuple[tuple[date, int], ...]
    wip_by_column: tuple[tuple[str, int], ...]
    cycle_time_by_column: tuple[tuple[str, float], ...]
    lead_time_hours_p50: float | None
    lead_time_hours_p90: float | None
    agent_pass_rate: float | None
    avg_review_rounds: float | None
    out_of_scope_rate: float | None
    avg_cost_per_task_usd: float | None
    run_outcomes: tuple[tuple[str, int], ...]
    blocked_count: int
    oldest_human_review_hours: float | None
    as_of: datetime


def _done_entries(facts: DevBoardFacts, window: DateRange) -> list[ColumnEntry]:
    return [e for e in facts.entries if e.column == "done" and window.contains(e.at)]


def _throughput(done: Sequence[ColumnEntry], window: DateRange) -> tuple[tuple[date, int], ...]:
    per_week: dict[date, set[uuid.UUID]] = defaultdict(set)
    for entry in done:
        per_week[bucket_start(entry.at, "week")].add(entry.task_id)
    first = bucket_start(window.start_at, "week")
    weeks = [first + timedelta(weeks=n) for n in range((window.end - first).days // 7 + 1)]
    weeks = [week for week in weeks if week < window.end][-MAX_WEEKS:]
    return tuple((week, len(per_week.get(week, set()))) for week in weeks)


def _first_done(done: Sequence[ColumnEntry]) -> dict[uuid.UUID, datetime]:
    first: dict[uuid.UUID, datetime] = {}
    for entry in sorted(done, key=lambda e: e.at):
        first.setdefault(entry.task_id, entry.at)
    return first


def _lead_times(tasks: Sequence[TaskFact], first_done: dict[uuid.UUID, datetime]) -> list[float]:
    created = {task.id: task.created_at for task in tasks}
    return [
        max(0.0, hours_between(created[task_id], at))
        for task_id, at in first_done.items()
        if task_id in created
    ]


def _review_stats(runs: Sequence[RunFact], window: DateRange) -> tuple[float | None, float | None]:
    reviews = [r for r in runs if r.kind == "review" and window.contains(r.finished_at)]
    first_round = [r for r in reviews if r.round == 0]
    passed = sum(1 for r in first_round if r.outcome == "approved")
    pass_rate = rounded(passed / len(first_round), 4) if first_round else None
    highest: dict[uuid.UUID, int] = {}
    for run in reviews:
        highest[run.task_id] = max(highest.get(run.task_id, 0), run.round)
    rounds = [value + 1 for value in highest.values()]
    return pass_rate, rounded(sum(rounds) / len(rounds), 4) if rounds else None


def _out_of_scope_rate(facts: DevBoardFacts, window: DateRange) -> float | None:
    latest: dict[uuid.UUID, tuple[datetime, bool]] = {}
    for record in facts.triage:
        if window.contains(record.at):
            seen = latest.get(record.task_id)
            if seen is None or record.at >= seen[0]:
                latest[record.task_id] = (record.at, record.in_scope)
    if not latest:
        return None
    out = sum(1 for _, in_scope in latest.values() if not in_scope)
    return rounded(out / len(latest), 4)


def _avg_cost(runs: Sequence[RunFact], task_ids: set[uuid.UUID]) -> float | None:
    if not task_ids:
        return None
    total = sum(run.cost_usd for run in runs if run.task_id in task_ids)
    return round(total / len(task_ids), 4)


def _run_outcomes(runs: Sequence[RunFact], window: DateRange) -> tuple[tuple[str, int], ...]:
    counts = Counter(
        OUTCOME_OF_STATUS.get(run.status, run.status)
        for run in runs
        if window.contains(run.created_at)
    )
    return tuple((outcome, counts[outcome]) for outcome in OUTCOMES)


def _oldest_human_review(facts: DevBoardFacts, now: datetime) -> float | None:
    waiting = [task for task in facts.tasks if task.column == "human_review"]
    if not waiting:
        return None
    entered: dict[uuid.UUID, datetime] = {}
    for entry in facts.entries:
        if entry.column == "human_review" and entry.at <= now:
            entered[entry.task_id] = max(entered.get(entry.task_id, entry.at), entry.at)
    since = [entered.get(task.id, task.created_at) for task in waiting]
    return rounded(max(0.0, hours_between(min(since), now)))


def dev_metrics(facts: DevBoardFacts, window: DateRange, now: datetime) -> DevMetricsView:
    done = _done_entries(facts, window)
    first_done = _first_done(done)
    leads = _lead_times(facts.tasks, first_done)
    columns = Counter(task.column for task in facts.tasks)
    samples = cycle_samples(facts.entries, window)
    pass_rate, review_rounds = _review_stats(facts.runs, window)
    return DevMetricsView(
        throughput=_throughput(done, window),
        wip_by_column=tuple((c, columns[c]) for c in TASK_COLUMNS if c not in NOT_IN_FLIGHT),
        cycle_time_by_column=tuple(
            (c, round(median, 2))
            for c in TASK_COLUMNS
            if (median := percentile(samples.get(c, []), 0.5)) is not None
        ),
        lead_time_hours_p50=rounded(percentile(leads, 0.5)),
        lead_time_hours_p90=rounded(percentile(leads, 0.9)),
        agent_pass_rate=pass_rate,
        avg_review_rounds=review_rounds,
        out_of_scope_rate=_out_of_scope_rate(facts, window),
        avg_cost_per_task_usd=_avg_cost(facts.runs, set(first_done)),
        run_outcomes=_run_outcomes(facts.runs, window),
        blocked_count=columns["blocked"],
        oldest_human_review_hours=_oldest_human_review(facts, now),
        as_of=now,
    )
