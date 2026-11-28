"""Job summary and dev-board metrics over hand-built facts (pure domain, no I/O)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from app.domain.jobs.dev_metrics import dev_metrics
from app.domain.jobs.metrics import DateRange
from app.domain.jobs.ports import (
    ColumnEntry,
    DevBoardFacts,
    RoleFact,
    RunFact,
    TaskFact,
    TriageFact,
)
from app.domain.jobs.summary import job_summary

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
WINDOW = DateRange(date(2026, 11, 2), date(2026, 11, 26), "week")


def _role(
    status: str,
    *,
    found_at: datetime = NOW - timedelta(hours=10),
    posted_at: datetime | None = None,
    applied_at: datetime | None = None,
    stage: str | None = None,
) -> RoleFact:
    return RoleFact(
        id=uuid.uuid4(),
        status=status,
        found_at=found_at,
        posted_at=posted_at,
        applied_at=applied_at,
        stage=stage,
    )


def test_summary_groups_statuses_and_excludes_filtered_from_act_now() -> None:
    roles = [
        _role("new", posted_at=NOW - timedelta(hours=30)),
        _role("reviewing", found_at=NOW - timedelta(hours=50)),
        _role("new", stage="filtered", posted_at=NOW - timedelta(hours=500)),
        _role("applied", posted_at=NOW - timedelta(hours=48), applied_at=NOW - timedelta(hours=24)),
        _role("interview", posted_at=NOW - timedelta(days=30), applied_at=NOW - timedelta(days=8)),
        _role("offer", stage="filtered"),
        _role("passed"),
        _role("closed"),
        _role("rejected"),
        _role("unknown"),
    ]
    view = job_summary(roles, NOW)
    assert view.tab_counts == {
        "act_now": 2,
        "applied": 1,
        "interviewing": 2,
        "passed": 2,
        "rejected": 1,
        "unknown": 1,
        "filtered": 1,
        "outreach": 0,
        "feedback": 0,
    }
    assert view.to_act_on == 2
    assert view.oldest_waiting_hours == 50.0
    assert view.applied_last_7_days == 1
    assert view.median_post_to_apply_hours == (24 + 22 * 24) / 2
    assert view.as_of == NOW


def test_summary_of_nothing_is_zeros_and_nulls() -> None:
    view = job_summary([], NOW)
    assert view.to_act_on == 0
    assert view.oldest_waiting_hours is None
    assert view.median_post_to_apply_hours is None
    assert set(view.tab_counts.values()) == {0}


def _at(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, tzinfo=UTC)


def test_dev_metrics_over_a_small_board() -> None:
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    facts = DevBoardFacts(
        tasks=(
            TaskFact(id=a, column="done", created_at=_at(3)),
            TaskFact(id=b, column="done", created_at=_at(10)),
            TaskFact(id=c, column="human_review", created_at=_at(20)),
            TaskFact(id=d, column="blocked", created_at=_at(21)),
        ),
        entries=(
            ColumnEntry(task_id=a, at=_at(3), column="backlog"),
            ColumnEntry(task_id=a, at=_at(4), column="agent_dev"),
            ColumnEntry(task_id=a, at=_at(5), column="done"),
            ColumnEntry(task_id=b, at=_at(10), column="agent_dev"),
            ColumnEntry(task_id=b, at=_at(12), column="done"),
            ColumnEntry(task_id=c, at=_at(24), column="human_review"),
        ),
        runs=(
            RunFact(
                task_id=a,
                kind="review",
                status="succeeded",
                round=0,
                cost_usd=1.0,
                created_at=_at(4),
                outcome="approved",
                finished_at=_at(4, 6),
            ),
            RunFact(
                task_id=b,
                kind="review",
                status="succeeded",
                round=0,
                cost_usd=2.0,
                created_at=_at(11),
                outcome="changes_requested",
                finished_at=_at(11, 1),
            ),
            RunFact(
                task_id=b,
                kind="review",
                status="succeeded",
                round=1,
                cost_usd=1.0,
                created_at=_at(11, 2),
                outcome="approved",
                finished_at=_at(11, 3),
            ),
            RunFact(
                task_id=c, kind="dev", status="timed_out", round=0, cost_usd=0.5, created_at=_at(22)
            ),
            RunFact(
                task_id=d, kind="dev", status="running", round=0, cost_usd=0.0, created_at=_at(23)
            ),
        ),
        triage=(
            TriageFact(task_id=a, at=_at(3), in_scope=False),
            TriageFact(task_id=a, at=_at(3, 1), in_scope=True),
            TriageFact(task_id=d, at=_at(21), in_scope=False),
        ),
    )
    view = dev_metrics(facts, WINDOW, NOW)
    assert view.throughput == (
        (date(2026, 11, 2), 1),
        (date(2026, 11, 9), 1),
        (date(2026, 11, 16), 0),
        (date(2026, 11, 23), 0),
    )
    assert dict(view.wip_by_column)["blocked"] == 1
    assert dict(view.wip_by_column)["human_review"] == 1
    assert "done" not in dict(view.wip_by_column)
    assert dict(view.cycle_time_by_column) == {"backlog": 24.0, "agent_dev": 36.0}
    assert view.lead_time_hours_p50 == 48.0  # a: 3rd -> 5th, b: 10th -> 12th
    assert view.lead_time_hours_p90 == 48.0
    assert view.agent_pass_rate == 0.5
    assert view.avg_review_rounds == 1.5
    assert not hasattr(view, "out_of_scope_rate")
    assert view.avg_cost_per_task_usd == 2.0
    assert dict(view.run_outcomes) == {
        "succeeded": 3,
        "failed": 1,
        "killed": 0,
        "running": 1,
        "queued": 0,
    }
    assert view.blocked_count == 1
    assert view.oldest_human_review_hours == 36.0


def test_dev_metrics_of_an_empty_board_is_nulls() -> None:
    view = dev_metrics(DevBoardFacts(), WINDOW, NOW)
    assert all(count == 0 for _, count in view.throughput)
    assert view.cycle_time_by_column == ()
    assert view.lead_time_hours_p50 is None
    assert view.lead_time_hours_p90 is None
    assert view.agent_pass_rate is None
    assert view.avg_review_rounds is None
    assert not hasattr(view, "out_of_scope_rate")
    assert view.avg_cost_per_task_usd is None
    assert view.oldest_human_review_hours is None
    assert view.blocked_count == 0
