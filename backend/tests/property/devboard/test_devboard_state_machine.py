"""No sequence of moves reaches an illegal state.

Random user moves (by either role, to any column, with or without a reason), system moves and
plan approvals run against the real `BoardService`; after every step the board must hold:
- `out_of_scope` never went straight to `ready`, and every `ready` card has an approved plan;
- only maintainers made maintainer moves; `agent_dev` is entered only from `ready` or
  `changes_requested`;
- blocked cards carry a reason and the column they came from;
- ranks are unique within each column (the ordering stays total).
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from app.adapters.fakes.devboard import InMemoryTaskStore
from app.adapters.fakes.security import FakePII
from app.adapters.fakes.system import FakeClock, SequentialIds
from app.domain.devboard.board import BoardService
from app.domain.devboard.model import (
    Actor,
    ActorKind,
    Column,
    EventKind,
    MoveRequest,
    PlanRecord,
    Risk,
)
from app.domain.devboard.state_machine import MAINTAINER_TARGETS, SYSTEM_MOVES
from app.domain.errors import DomainError
from app.domain.identity import AppRole

MAINTAINER = Actor(uuid.UUID(int=1), ActorKind.MAINTAINER, AppRole.MAINTAINER)
USER = Actor(uuid.UUID(int=2), ActorKind.USER, AppRole.USER)
SYSTEM = Actor(uuid.UUID(int=1), ActorKind.SYSTEM, AppRole.MAINTAINER)
NOW = datetime(2026, 11, 25, tzinfo=UTC)

columns = st.sampled_from(list(Column))
user_move = st.tuples(
    st.just("user"),
    st.integers(0, 2),
    columns,
    st.booleans(),  # is the maintainer
    st.sampled_from([None, "", "waiting on design"]),
    st.sampled_from(["0", "U", "z", "U0", "k"]),
)
system_move = st.tuples(st.just("system"), st.integers(0, 2), columns)
approve = st.tuples(st.just("approve"), st.integers(0, 2))
actions = st.lists(st.one_of(user_move, system_move, approve), max_size=40)


@settings(max_examples=200, deadline=None)
@given(actions)
def test_no_sequence_of_moves_reaches_an_illegal_state(steps: list[tuple[object, ...]]) -> None:
    asyncio.run(_play(steps))


async def _play(steps: list[tuple[object, ...]]) -> None:
    clock, ids = FakeClock(NOW), SequentialIds()
    store = InMemoryTaskStore()
    board = BoardService(store, FakePII(), clock, ids)
    cards = [(await board.create(USER, f"card {n}", "", ())).id for n in range(3)]
    for step in steps:
        task_id = cards[int(str(step[1]))]
        with contextlib.suppress(DomainError):
            await _apply(board, store, step, task_id, ids)
        await _check(store, cards)


async def _apply(
    board: BoardService,
    store: InMemoryTaskStore,
    step: tuple[object, ...],
    task_id: uuid.UUID,
    ids: SequentialIds,
) -> None:
    task = await board.get(MAINTAINER, task_id)
    if step[0] == "user":
        _, _, target, is_maintainer, reason, rank = step
        actor = MAINTAINER if is_maintainer else USER
        assert isinstance(target, Column)
        move = MoveRequest(
            target, str(rank), task.version, reason if isinstance(reason, str) else None
        )
        await board.move(actor, task_id, move)
    elif step[0] == "system":
        target = step[2]
        assert isinstance(target, Column)
        await board.system_move(SYSTEM, task_id, target, reason="system")
    elif task.column is Column.PLAN_REVIEW:
        plan = PlanRecord(ids.new_id(), task_id, 1, "single", (), (), 1.0, 10, Risk.LOW, NOW)
        await store.add_plan(
            MAINTAINER.user_id,
            replace(plan, tests_approved_at=plan.created_at, plan_approved_at=plan.created_at),
        )


async def _check(store: InMemoryTaskStore, cards: list[uuid.UUID]) -> None:
    for task_id in cards:
        task = await store.get(MAINTAINER.user_id, task_id)
        assert task is not None
        if task.column is Column.READY:
            plan = await store.latest_plan(MAINTAINER.user_id, task_id)
            assert plan is not None
            assert plan.is_approved
        if task.column is Column.BLOCKED:
            assert task.blocked_reason
            assert task.blocked_from is not None
    for event in store.events_log:
        if event.kind is not EventKind.MOVED or event.from_column is None:
            continue
        pair = (event.from_column, event.to_column)
        assert pair != (Column.OUT_OF_SCOPE, Column.READY)
        if event.actor_kind is ActorKind.USER:
            assert event.to_column not in MAINTAINER_TARGETS.get(event.from_column, frozenset())
        if event.to_column is Column.AGENT_DEV:
            assert event.from_column in {Column.READY, Column.CHANGES_REQUESTED}
            is_system_edge = Column.AGENT_DEV in SYSTEM_MOVES.get(event.from_column, frozenset())
            assert event.actor_kind is ActorKind.MAINTAINER or is_system_edge
    by_column = Counter((task.column, task.rank) for task in store.tasks.values())
    assert all(count == 1 for count in by_column.values())
