"""What dev-board routes get from `app.compose.devboard`, plus their auth dependencies."""

# No `from __future__ import annotations`: FastAPI must evaluate the `Depends(...)` aliases.

from dataclasses import dataclass
from typing import Annotated, Protocol

from fastapi import Depends, Request

from app.api.security import maintainer, rate_limited_user
from app.domain.devboard.board import BoardService
from app.domain.devboard.model import Actor
from app.domain.devboard.plans import PlanService
from app.domain.devboard.reviews import ReviewService
from app.domain.devboard.runs import RunService
from app.domain.devboard.task_chat import TaskChatService
from app.domain.devboard.task_chat_decisions import ProposalDecisions
from app.domain.devboard.workflow import Workflow
from app.domain.errors import NotFound
from app.domain.identity import Principal
from app.domain.rate_limits import RateTier


class StepRedactor(Protocol):
    async def redact_or_none(self, text: str) -> str | None: ...


@dataclass(frozen=True)
class DevboardServices:
    board: BoardService
    runs: RunService
    plans: PlanService
    workflow: Workflow
    redactor: StepRedactor
    chat: TaskChatService
    proposals: ProposalDecisions
    # Human review reads. `None` until the runtime wires a code host (BranchViews).
    reviews: ReviewService | None = None


def devboard(request: Request) -> DevboardServices:
    found = request.app.state.devboard
    assert isinstance(found, DevboardServices)
    return found


Services = Annotated[DevboardServices, Depends(devboard)]


def reviews(services: Services) -> ReviewService:
    if services.reviews is None:
        raise NotFound("Reviews aren't available on this server.")
    return services.reviews


Reviews = Annotated[ReviewService, Depends(reviews)]
DefaultUser = Annotated[Principal, Depends(rate_limited_user(RateTier.DEFAULT))]
AiUser = Annotated[Principal, Depends(rate_limited_user(RateTier.AI))]
StreamUser = Annotated[Principal, Depends(rate_limited_user(RateTier.STREAM))]
DefaultMaintainer = Annotated[Principal, Depends(rate_limited_user(RateTier.DEFAULT, maintainer))]
AgentMaintainer = Annotated[Principal, Depends(rate_limited_user(RateTier.AGENT, maintainer))]


def actor(principal: Principal) -> Actor:
    return Actor.of(principal)
