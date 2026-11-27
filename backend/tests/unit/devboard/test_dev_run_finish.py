"""A dev run whose card left `agent_dev` while it ran (the agent asked for help, or a human
moved it) still finishes with its outcome and spend; the card stays where it is.

Regression: the agent's handoff was refused, it called request_help (card -> blocked) and kept
working; at the end the workflow's move `blocked -> agent_review` raised, the run failed with
"That move isn't allowed from this column." and its spend was recorded as $0.00.
"""

from __future__ import annotations

from dataclasses import replace

from app.domain.devboard.board import BoardService
from app.domain.devboard.model import Column, RunKind, RunStatus
from app.domain.ports.devboard import AgentResult, RunContext, RunTools
from tests.unit.devboard.workflow_harness import MAINTAINER, Harness

HELP = "Blocked: the handoff was refused."


class _AsksForHelp:
    """Moves its card to blocked mid-run (as the coordinator's request_help does)."""

    def __init__(self, *, is_success: bool) -> None:
        self.board: BoardService | None = None
        self._is_success = is_success

    async def run(self, context: RunContext, tools: RunTools) -> AgentResult:
        assert self.board is not None
        assert not await tools.is_cancelled()
        await self.board.system_move(
            MAINTAINER.as_agent(), context.task.id, Column.BLOCKED, reason=HELP
        )
        return AgentResult(is_success=self._is_success, outcome="Work finished.", cost_usd=1.25)


async def _dev_run_on_a_card_that_gets_blocked(*, is_success: bool) -> Harness:
    runner = _AsksForHelp(is_success=is_success)
    harness = Harness(runner=runner)
    runner.board = harness.board
    task = await harness.create("Remove out-of-scope rate")
    current = await harness.board.get(MAINTAINER, task.id)
    dev = await harness.tasks.save(
        MAINTAINER.user_id, replace(current, column=Column.AGENT_DEV), current.version
    )
    await harness.workflow.on_moved(MAINTAINER, dev, Column.READY)
    await harness.dispatcher.drain()
    return harness


async def test_the_run_keeps_its_spend_and_outcome_when_the_card_was_blocked() -> None:
    harness = await _dev_run_on_a_card_that_gets_blocked(is_success=True)
    task = next(iter(await harness.tasks.list(MAINTAINER.user_id, (Column.BLOCKED,), 0, 5)))
    run = await harness.latest_run(task, RunKind.DEV)
    assert run.status is RunStatus.SUCCEEDED
    assert run.cost_usd == 1.25
    assert run.outcome is not None
    assert "Work finished." in run.outcome
    assert "isn't allowed" not in run.outcome


async def test_the_card_stays_blocked_with_the_agents_reason() -> None:
    for is_success in (True, False):
        harness = await _dev_run_on_a_card_that_gets_blocked(is_success=is_success)
        blocked = await harness.tasks.list(MAINTAINER.user_id, (Column.BLOCKED,), 0, 5)
        assert len(blocked) == 1
        assert blocked[0].blocked_reason == HELP
        run = await harness.latest_run(blocked[0], RunKind.DEV)
        assert run.cost_usd == 1.25
