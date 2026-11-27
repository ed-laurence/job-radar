"""Dev-board columns and the per-column time samples shared by the dashboard series."""

from __future__ import annotations

import itertools
from collections import defaultdict
from collections.abc import Sequence

from app.domain.jobs.metrics import DateRange, hours_between
from app.domain.jobs.ports import ColumnEntry

TASK_COLUMNS = (
    "backlog",
    "triaged",
    "out_of_scope",
    "plan_review",
    "ready",
    "agent_dev",
    "agent_review",
    "changes_requested",
    "human_review",
    "blocked",
    "done",
)
NOT_IN_FLIGHT = frozenset({"backlog", "out_of_scope", "done"})


def cycle_samples(entries: Sequence[ColumnEntry], window: DateRange) -> dict[str, list[float]]:
    by_task: dict[object, list[ColumnEntry]] = defaultdict(list)
    for entry in entries:
        by_task[entry.task_id].append(entry)
    samples: dict[str, list[float]] = defaultdict(list)
    for task_entries in by_task.values():
        ordered = sorted(task_entries, key=lambda entry: entry.at)
        for current, following in itertools.pairwise(ordered):
            if window.contains(following.at):
                samples[current.column].append(hours_between(current.at, following.at))
    return samples
