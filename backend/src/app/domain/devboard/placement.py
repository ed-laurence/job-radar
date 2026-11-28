"""Where a card lands in a column: the client's rank when it is free, otherwise the next key
after it, otherwise a rebalance of the whole column. Ordering stays total: no two live cards
in a column ever share a rank."""

from __future__ import annotations

import uuid

from app.domain.devboard.model import Column
from app.domain.devboard.ranking import key_after, key_between, spread
from app.domain.ports.devboard import TaskStore


async def place_at(
    store: TaskStore, user_id: uuid.UUID, column: Column, task_id: uuid.UUID, requested: str
) -> str:
    others = [(rank, other) for rank, other in await store.column_ranks(user_id, column)]
    others = [(rank, other) for rank, other in others if other != task_id]
    ranks = [rank for rank, _ in others]
    if requested not in ranks:
        return requested
    successor = min((rank for rank in ranks if rank > requested), default=None)
    candidate = key_between(requested, successor)
    if candidate is not None:
        return candidate
    after = ranks.index(requested)
    order = [other for _, other in others]
    order.insert(after + 1, task_id)
    return await _rebalance(store, user_id, order, task_id)


async def place_last(
    store: TaskStore, user_id: uuid.UUID, column: Column, task_id: uuid.UUID
) -> str:
    others = [(rank, other) for rank, other in await store.column_ranks(user_id, column)]
    others = [(rank, other) for rank, other in others if other != task_id]
    candidate = key_after(others[-1][0] if others else None)
    if candidate is not None:
        return candidate
    order = [other for _, other in others] + [task_id]
    return await _rebalance(store, user_id, order, task_id)


async def place_first(
    store: TaskStore, user_id: uuid.UUID, column: Column, task_id: uuid.UUID
) -> str:
    others = [(rank, other) for rank, other in await store.column_ranks(user_id, column)]
    others = [(rank, other) for rank, other in others if other != task_id]
    candidate = key_between(None, others[0][0]) if others else key_after(None)
    if candidate is not None:
        return candidate
    order = [task_id] + [other for _, other in others]
    return await _rebalance(store, user_id, order, task_id)


async def _rebalance(
    store: TaskStore, user_id: uuid.UUID, order: list[uuid.UUID], moving: uuid.UUID
) -> str:
    keys = dict(zip(order, spread(len(order)), strict=True))
    await store.set_ranks(user_id, {task: key for task, key in keys.items() if task != moving})
    return keys[moving]
