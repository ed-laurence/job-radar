import threading

import pytest
from llm_proxy.budget import BudgetExceededError, BudgetLedger


def test_reserve_then_settle_records_real_usage() -> None:
    ledger = BudgetLedger()
    r = ledger.reserve("T-1", 100, 60)
    assert ledger.settle(r, 15) == 15
    assert ledger.used("T-1") == 15


def test_reservation_blocks_overrun() -> None:
    ledger = BudgetLedger()
    ledger.reserve("T-1", 100, 60)
    with pytest.raises(BudgetExceededError):
        ledger.reserve("T-1", 100, 50)


def test_exhausted_budget_refuses() -> None:
    ledger = BudgetLedger()
    ledger.settle(ledger.reserve("T-1", 100, 100), 100)
    with pytest.raises(BudgetExceededError):
        ledger.reserve("T-1", 100, 1)


def test_tasks_are_independent() -> None:
    ledger = BudgetLedger()
    ledger.settle(ledger.reserve("T-1", 100, 100), 100)
    ledger.reserve("T-2", 100, 100)


def test_parallel_reservations_never_exceed_budget() -> None:
    ledger = BudgetLedger()
    granted: list[int] = []

    def worker() -> None:
        try:
            ledger.reserve("T-1", 1000, 10)
            granted.append(1)
        except BudgetExceededError:
            pass

    threads = [threading.Thread(target=worker) for _ in range(200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(granted) == 100
