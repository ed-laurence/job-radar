"""Strict request schemas and the message caps (PLAN "Keeping agent communication concise").

Every request model forbids extra fields, so a request can never point at another task or run:
those come only from the token. A message over a cap is refused with `MessageTooLarge` (413),
never cut; any other schema violation is `ValidationFailed` (422).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.devboard.messages import Handoff
from app.domain.errors import FieldError, ValidationFailed

MAX_STEP_CHARS = 2000
MAX_HELP_CHARS = 2000
MAX_WAIT_SECONDS = 60
MAX_CLAIM_PATHS = 50
RESULT_TOKENS = 2000
MAX_BODY_BYTES = 64 * 1024
MAX_CURSOR = 10_000

# Pydantic error types and cap-validator messages that mean "too big", not "wrong shape".
_SIZE_TYPES = frozenset({"string_too_long", "too_long"})
_CAP_MARKERS = ("the cap is", "words", "at most")


class MessageTooLarge(ValidationFailed):
    code = "message-too-large"


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ClaimRequest(_Input):
    paths: list[str] = Field(min_length=1, max_length=MAX_CLAIM_PATHS)
    wait_s: int = Field(default=0, ge=0, le=MAX_WAIT_SECONDS)


class RenewRequest(_Input):
    pass


class ReleaseRequest(_Input):
    paths: list[str] | None = Field(default=None, min_length=1, max_length=MAX_CLAIM_PATHS)


class StepRequest(_Input):
    kind: Literal["thought", "tool_call", "tool_result", "message"]
    text: str = Field(min_length=1, max_length=MAX_STEP_CHARS)


class HelpRequest(_Input):
    """`text` is also held to the request_help cap (200 tokens) by the service."""

    text: str = Field(min_length=1, max_length=MAX_HELP_CHARS)


class AgentHandoff(Handoff):
    """The AGENTS.md handoff: the board's `Handoff` plus `skipped_checks` (same caps)."""

    skipped_checks: list[str] = Field(default_factory=list[str], max_length=10)


def _is_size(item: dict[str, object]) -> bool:
    kind, message = str(item.get("type", "")), str(item.get("msg", ""))
    return kind in _SIZE_TYPES or (
        kind == "value_error" and any(marker in message for marker in _CAP_MARKERS)
    )


def parse_capped[T: BaseModel](model: type[T], raw: str | bytes) -> T:
    """Validate a JSON message, or refuse it: `MessageTooLarge` when any error is a size cap,
    else `ValidationFailed`. Field errors point into the message and never echo input."""
    try:
        return model.model_validate_json(raw)
    except ValidationError as error:
        items = error.errors(include_input=False, include_url=False)
        errors = [
            FieldError(
                pointer="/" + "/".join(str(part) for part in item["loc"]),
                message=str(item["msg"])[:300],
            )
            for item in items
        ]
        too_large = any(_is_size(dict(item)) for item in items)
        refusal = MessageTooLarge if too_large else ValidationFailed
        raise refusal(f"{model.__name__} refused.", errors=errors) from None
