"""The in-app triage agent shows the model the repo listing and the open cards (as refs), asks
once more with a `path_problems` block when the answer names paths that aren't in the repo,
and then fails the run naming them."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast

import pytest
from pydantic import BaseModel

from app.adapters.codehost.working_tree import ListedRepoFiles
from app.adapters.fakes.security import FakeContentGuard, FakePII
from app.agents.triage.agent import TriageLlmAgent, open_cards_text
from app.agents.triage.schema import TriageOutput
from app.domain.devboard.model import (
    Actor,
    ActorKind,
    Column,
    NewStep,
    Route,
    RunKind,
    RunRecord,
    RunStatus,
    StepRecord,
    TaskRecord,
    VerificationStatus,
)
from app.domain.errors import ValidationFailed
from app.domain.ports import FinalAnswer, LlmRequest, LlmResult, ToolCall, ToolTurn
from app.domain.ports.devboard import CardRef, LeaseOutcome, RunContext
from app.settings import Settings
from tests.unit.devboard import cards
from tests.unit.devboard.workflow_harness import FILES, StaticFiles

NOW = datetime(2026, 11, 25, tzinfo=UTC)
GOOD: dict[str, object] = cards.URGENT_FIX.answer | {"likely_paths": ["web/src/features/devboard/"]}
INVENTED: dict[str, object] = GOOD | {"likely_paths": ["web/src/components/board/Board.tsx"]}


def _verification(**changes: object) -> dict[str, object]:
    found = cast("dict[str, object]", GOOD["verification"])
    return GOOD | {"verification": found | changes}


class ScriptedLlm:
    def __init__(self, *answers: dict[str, object]) -> None:
        self.answers = list(answers)
        self.requests: list[LlmRequest] = []

    async def generate[T: BaseModel](self, request: LlmRequest, output: type[T]) -> LlmResult[T]:
        self.requests.append(request)
        value = output.model_validate(self.answers.pop(0))
        return LlmResult(value=value, provider="replay", model="scripted", is_fallback=False)

    def stream(self, request: LlmRequest) -> AsyncIterator[str]:
        raise AssertionError(f"triage never streams: {request.feature}")

    async def next_action[T: BaseModel](
        self, request: LlmRequest, turn: ToolTurn, answer: type[T]
    ) -> ToolCall | FinalAnswer[T]:
        raise AssertionError(f"triage uses no tools: {request.feature} {turn} {answer}")


class Steps:
    def __init__(self) -> None:
        self.steps: list[NewStep] = []

    async def emit(self, step: NewStep) -> StepRecord:
        self.steps.append(step)
        return StepRecord(uuid.uuid4(), uuid.UUID(int=9), len(self.steps), step.kind, "", NOW)

    async def claim_paths(self, paths: tuple[str, ...]) -> LeaseOutcome:
        raise AssertionError(f"triage claims no paths: {paths}")

    async def renew_leases(self) -> int:
        return 0

    async def release_paths(self) -> int:
        return 0

    async def is_cancelled(self) -> bool:
        return False


OPEN = (
    CardRef(uuid.UUID(int=21), "Fix the board", Column.TRIAGED),
    CardRef(uuid.UUID(int=22), "Search contacts", Column.BACKLOG),
)


def _context(open_cards: tuple[CardRef, ...] = OPEN) -> RunContext:
    task = TaskRecord(
        id=uuid.UUID(int=1),
        title=cards.URGENT_FIX.title,
        body=cards.URGENT_FIX.body,
        column=Column.BACKLOG,
        rank="U",
        version=1,
        created_by=uuid.UUID(int=2),
        created_at=NOW,
        updated_at=NOW,
    )
    run = RunRecord(uuid.UUID(int=3), task.id, RunKind.TRIAGE, RunStatus.RUNNING, NOW)
    actor = Actor(uuid.UUID(int=2), ActorKind.MAINTAINER)
    return RunContext(
        run=run, task=task, actor=actor, plan=None, triage=None, open_cards=open_cards
    )


def _agent(llm: ScriptedLlm) -> TriageLlmAgent:
    return TriageLlmAgent(llm, FakeContentGuard(), FakePII(), StaticFiles())


def _blocks(request: LlmRequest) -> dict[str, str]:
    return {block.name: block.text for block in request.data}


async def test_the_model_sees_the_task_the_listing_and_the_open_cards() -> None:
    llm = ScriptedLlm(GOOD)
    result = await _agent(llm).triage(_context(), Steps())
    blocks = _blocks(llm.requests[0])
    assert list(blocks) == ["task", "repo_files", "open_cards"]
    assert blocks["repo_files"].splitlines() == sorted(FILES)
    assert blocks["open_cards"] == "C1 | triaged | Fix the board\nC2 | backlog | Search contacts"
    assert (result.route, result.verification.status) == (Route.NOW, VerificationStatus.CONFIRMED)


def test_no_open_cards_is_said_plainly() -> None:
    assert open_cards_text(()) == "(no other open cards)"


async def test_a_duplicate_ref_maps_back_to_the_card_id() -> None:
    answer = _verification(status="duplicate", duplicate_of="C2")
    result = await _agent(ScriptedLlm(answer)).triage(_context(), Steps())
    assert result.verification.duplicate_of == OPEN[1].id


async def test_a_ref_to_no_open_card_fails() -> None:
    answer = _verification(status="duplicate", duplicate_of="C9")
    with pytest.raises(ValidationFailed) as caught:
        await _agent(ScriptedLlm(answer)).triage(_context(), Steps())
    assert "not an open card: C9" in str(caught.value.detail)


async def test_invented_paths_are_asked_again_once_with_the_problems() -> None:
    llm = ScriptedLlm(INVENTED, GOOD)
    result = await _agent(llm).triage(_context(), Steps())
    assert result.likely_paths == ("web/src/features/devboard/",)
    assert len(llm.requests) == 2
    retry = _blocks(llm.requests[1])
    assert list(retry)[-1] == "path_problems"
    assert "web/src/components/board/Board.tsx" in retry["path_problems"]


async def test_a_second_bad_answer_fails_naming_the_paths() -> None:
    llm = ScriptedLlm(INVENTED, INVENTED)
    with pytest.raises(ValidationFailed) as caught:
        await _agent(llm).triage(_context(), Steps())
    assert caught.value.detail == (
        "Triage named paths that are not in the repo: web/src/components/board/Board.tsx"
    )
    assert len(llm.requests) == 2


async def test_invented_code_refs_count_too() -> None:
    answer = _verification(code_refs=["web/src/nowhere/X.tsx"])
    with pytest.raises(ValidationFailed):
        await _agent(ScriptedLlm(answer, answer)).triage(_context(), Steps())


async def test_an_out_of_scope_answer_may_name_paths_outside_the_listing() -> None:
    llm = ScriptedLlm(cards.INFRA_BACKUP.answer)
    result = await _agent(llm).triage(_context(), Steps())
    assert result.out_of_scope_reason
    assert len(llm.requests) == 1


@pytest.mark.parametrize("card", cards.CARDS, ids=lambda card: card.title)
async def test_every_fixture_answer_passes_the_check_against_the_frozen_listing(
    card: cards.Card,
) -> None:
    listing = ListedRepoFiles(Settings().llm_fixtures_dir / "devboard" / "repo_files.txt")
    llm = ScriptedLlm(card.answer)
    agent = TriageLlmAgent(llm, FakeContentGuard(), FakePII(), listing)
    await agent.triage(_context(), Steps())
    assert len(llm.requests) == 1
    TriageOutput.model_validate(card.answer)
