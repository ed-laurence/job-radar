"""PR sync against the Postgres task store under the app role (FORCE RLS): the url, the
`pr-closed` label and the merge move to done persist, and a second pass changes nothing."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.devboard_tasks import PgTaskStore
from app.adapters.fakes.pull_requests import FakePullRequests
from app.adapters.fakes.security import FakePII
from app.adapters.fakes.system import FakeClock
from app.adapters.system import UuidIds
from app.domain.devboard.board import BoardService
from app.domain.devboard.model import Actor, ActorKind, Column, EventKind, TaskRecord
from app.domain.devboard.pr_sync import PR_CLOSED_LABEL, PrSyncService
from app.domain.identity import AppRole
from app.domain.ports.devboard import PullRequest, PullRequestState, github_branch
from tests.integration.devboard.cleanup import leave_no_rows

pytestmark = pytest.mark.integration
__all__ = ["leave_no_rows"]  # the autouse fixture

type Sessions = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 11, 28, 1, 0, tzinfo=UTC)
URL = "https://github.com/acme/radar/pull/"


def _card(column: Column, owner: uuid.UUID, rank: str) -> TaskRecord:
    return TaskRecord(
        uuid.uuid4(),
        "Card",
        "Body",
        column,
        rank,
        1,
        owner,
        NOW,
        NOW,
        labels=("ui",),
        branch="agent/scratch",
    )


async def test_a_pass_persists_and_a_second_changes_nothing(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    maintainer, user = two_users
    actor = Actor(maintainer, ActorKind.SYSTEM, AppRole.MAINTAINER)
    tasks = PgTaskStore(sessions)
    merged = _card(Column.HUMAN_REVIEW, user, "U1")
    closed = _card(Column.AGENT_REVIEW, user, "U2")
    for task in (merged, closed):
        await tasks.insert(user, task)
    prs = FakePullRequests()
    prs.put(github_branch(merged.id), PullRequest(f"{URL}1", 1, PullRequestState.MERGED))
    prs.put(github_branch(closed.id), PullRequest(f"{URL}2", 2, PullRequestState.CLOSED))
    board = BoardService(tasks, FakePII(), FakeClock(), UuidIds())
    service = PrSyncService(tasks, board, prs)

    first = await service.sync(actor)
    second = await service.sync(actor)

    done = await tasks.get(maintainer, merged.id)
    labelled = await tasks.get(maintainer, closed.id)
    assert done is not None
    assert labelled is not None
    assert (done.column, done.pr_url) == (Column.DONE, f"{URL}1")
    assert (labelled.column, labelled.pr_url) == (Column.AGENT_REVIEW, f"{URL}2")
    assert labelled.labels == ("ui", PR_CLOSED_LABEL)
    assert (first.moved_to_done, first.labelled) == (1, 1)
    assert (second.moved_to_done, second.labelled, second.urls_recorded) == (0, 0, 0)
    events = await tasks.events(maintainer, merged.id, 0, 10)
    assert [event.kind for event in events] == [EventKind.MOVED]
    assert labelled.version == 2
