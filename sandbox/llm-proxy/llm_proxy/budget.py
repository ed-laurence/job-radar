"""Per-task token budgets (in memory; a proxy restart resets usage, tokens still expire)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


class BudgetExceededError(Exception):
    pass


@dataclass
class _Task:
    used: int = 0
    reserved: int = 0


@dataclass(frozen=True)
class Reservation:
    task_id: str
    amount: int


@dataclass
class BudgetLedger:
    """Reserve ``max_tokens`` before a call, settle with the real usage afterwards.

    Reservations stop parallel requests from jointly overrunning the budget.
    """

    _tasks: dict[str, _Task] = field(default_factory=dict[str, _Task])
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def reserve(self, task_id: str, budget: int, amount: int) -> Reservation:
        with self._lock:
            task = self._tasks.setdefault(task_id, _Task())
            if task.used + task.reserved + amount > budget:
                raise BudgetExceededError(task_id)
            task.reserved += amount
            return Reservation(task_id, amount)

    def settle(self, reservation: Reservation, used: int) -> int:
        """Release the reservation and record ``used`` tokens. Returns the task's total."""
        with self._lock:
            task = self._tasks.setdefault(reservation.task_id, _Task())
            task.reserved = max(0, task.reserved - reservation.amount)
            task.used += max(0, used)
            return task.used

    def used(self, task_id: str) -> int:
        with self._lock:
            task = self._tasks.get(task_id)
            return task.used if task else 0
