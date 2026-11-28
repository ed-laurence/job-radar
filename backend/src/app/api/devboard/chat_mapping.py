"""Task chat records -> generated response models. Absent values are omitted ("no nulls")."""

from __future__ import annotations

from app.api.devboard import mapping
from app.domain.devboard.model import Actor, Page, StepRecord
from app.domain.devboard.proposals import ProposalRecord, changes_to_dict
from app.domain.devboard.task_chat import ChatMessageView
from app.domain.devboard.task_chat_decisions import AcceptOutcome
from app.generated.models import (
    Step,
    TaskChatMessage,
    TaskChatMessagePage,
    TaskChatProposal,
    TaskChatProposalAccepted,
)


def proposal(record: ProposalRecord) -> TaskChatProposal:
    return TaskChatProposal.model_validate(
        {
            "id": record.id,
            "task_id": record.task_id,
            "status": record.status.value,
            "reason": record.reason,
            "changes": changes_to_dict(record.changes),
            "base_version": record.base_version,
            "created_at": record.created_at,
            "decided_at": record.decided_at,
        }
    )


def message(view: ChatMessageView) -> TaskChatMessage:
    record = view.message
    return TaskChatMessage.model_validate(
        {
            "id": record.id,
            "author": record.author,
            "content": record.content,
            "proposal": proposal(view.proposal) if view.proposal else None,
            "created_at": record.created_at,
        }
    )


def message_page(page: Page[ChatMessageView]) -> TaskChatMessagePage:
    return TaskChatMessagePage(
        items=[message(view) for view in page.items], next_cursor=page.next_cursor
    )


def accepted(outcome: AcceptOutcome, viewer: Actor) -> TaskChatProposalAccepted:
    return TaskChatProposalAccepted(
        proposal=proposal(outcome.proposal),
        task=mapping.task(outcome.task, viewer),
        returned_to_backlog=outcome.returned_to_backlog,
        note=outcome.note,
    )


def step(record: StepRecord) -> Step:
    return mapping.step(record, record.summary)
