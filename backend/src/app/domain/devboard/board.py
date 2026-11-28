"""Dev-board cards: create, read, update, soft delete and moves (spec "Dev board").

Task text is untrusted: PII is redacted before it is saved (SEC-05) and the author is told
through `pii_redacted`. Moves go through the state machine; workflow side effects (triage,
plans, dev runs) are the `BoardHooks` implementation's job.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from app.domain.devboard.model import (
    Actor,
    Column,
    EventKind,
    MoveRequest,
    Page,
    TaskEdit,
    TaskEventRecord,
    TaskQuery,
    TaskRecord,
    TriageRecord,
)
from app.domain.devboard.placement import place_at, place_last
from app.domain.devboard.state_machine import (
    check_system_move,
    check_user_move,
    edit_returns_to_backlog,
)
from app.domain.errors import (
    BadRequest,
    Conflict,
    Forbidden,
    IllegalTransition,
    NotFound,
    PlanNotApproved,
    ValidationFailed,
)
from app.domain.ports import PII, Clock, Ids
from app.domain.ports.devboard import TaskStore

REDACTED = "[redacted]"
MODIFY_TESTS = "modify-tests:"
MAINTAINER_LABEL_PREFIXES = (MODIFY_TESTS, "allow:")
# The spec's limits on `labels` (TaskCreate / TaskUpdate): count and characters per label.
MAX_LABELS = 20
MAX_LABEL_CHARS = 100


class BoardHooks(Protocol):
    async def on_created(self, actor: Actor, task: TaskRecord) -> None: ...

    async def on_text_changed(self, actor: Actor, task: TaskRecord) -> None: ...

    async def on_moved(
        self, actor: Actor, task: TaskRecord, from_column: Column, *, reason: str | None = None
    ) -> None:
        """After a user move. `reason` is the mover's note (a block, or review notes)."""
        ...


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise BadRequest("The cursor is not valid.") from None
    prefix, _, number = raw.partition(":")
    if prefix != "o" or not number.isdigit():
        raise BadRequest("The cursor is not valid.")
    return int(number)


async def redact_pii(pii: PII, text: str) -> tuple[str, bool]:
    spans = await pii.find(text)
    if not spans:
        return text, False
    pieces: list[str] = []
    cursor = 0
    for span in spans:
        if span.start < cursor:
            continue
        pieces.extend((text[cursor : span.start], REDACTED))
        cursor = span.end
    pieces.append(text[cursor:])
    return "".join(pieces), True


class BoardService:
    def __init__(self, store: TaskStore, pii: PII, clock: Clock, ids: Ids) -> None:
        self._store = store
        self._pii = pii
        self._clock = clock
        self._ids = ids
        self._hooks: BoardHooks | None = None

    def attach(self, hooks: BoardHooks) -> None:
        self._hooks = hooks

    # ------------------------------------------------------------ reads
    async def get(self, actor: Actor, task_id: uuid.UUID) -> TaskRecord:
        task = await self._store.get(actor.user_id, task_id)
        if task is None:
            raise NotFound("Task not found.")
        return task

    async def list(self, actor: Actor, query: TaskQuery) -> Page[TaskRecord]:
        offset = decode_cursor(query.cursor)
        rows = await self._store.list(actor.user_id, query.columns, offset, query.limit + 1)
        more = len(rows) > query.limit
        cursor = encode_cursor(offset + query.limit) if more else None
        return Page(items=rows[: query.limit], next_cursor=cursor)

    async def latest_triage(self, actor: Actor, task_id: uuid.UUID) -> TriageRecord | None:
        await self.get(actor, task_id)
        return await self._store.latest_triage(actor.user_id, task_id)

    async def events(
        self, actor: Actor, task_id: uuid.UUID, cursor: str | None, limit: int
    ) -> Page[TaskEventRecord]:
        await self.get(actor, task_id)
        offset = decode_cursor(cursor)
        rows = await self._store.events(actor.user_id, task_id, offset, limit + 1)
        more = len(rows) > limit
        return Page(items=rows[:limit], next_cursor=encode_cursor(offset + limit) if more else None)

    async def without_pii(self, text: str) -> str:
        """Untrusted text (like a human's review notes) with PII replaced, as card text is
        before it is saved (SEC-05)."""
        clean, _ = await redact_pii(self._pii, text)
        return clean

    # ------------------------------------------------------------ writes
    async def create(
        self, actor: Actor, title: str, body: str | None, labels: tuple[str, ...]
    ) -> TaskRecord:
        _check_labels(actor, labels, ())
        clean_title, title_hit = await redact_pii(self._pii, title)
        clean_body, body_hit = await redact_pii(self._pii, body or "")
        now = self._clock.now()
        task_id = self._ids.new_id()
        task = TaskRecord(
            id=task_id,
            title=clean_title,
            body=clean_body,
            column=Column.BACKLOG,
            rank=await place_last(self._store, actor.user_id, Column.BACKLOG, task_id),
            version=1,
            created_by=actor.user_id,
            created_by_role=actor.role,
            created_at=now,
            updated_at=now,
            labels=labels,
            pii_redacted=title_hit or body_hit,
        )
        await self._store.insert(actor.user_id, task)
        await self._event(actor, task, EventKind.CREATED)
        if task.pii_redacted:
            await self._event(actor, task, EventKind.PII_REDACTED)
        if self._hooks is not None:
            await self._hooks.on_created(actor, task)
        return await self.get(actor, task_id)

    async def update(self, actor: Actor, task_id: uuid.UUID, edit: TaskEdit) -> TaskRecord:
        task = await self.get(actor, task_id)
        labels = task.labels if edit.labels is None else edit.labels
        _check_labels(actor, labels, task.labels)
        title, title_hit = task.title, False
        body, body_hit = task.body, False
        if edit.title is not None and edit.title != task.title:
            title, title_hit = await redact_pii(self._pii, edit.title)
        if edit.body is not None and edit.body != task.body:
            body, body_hit = await redact_pii(self._pii, edit.body)
        is_text_change = (title, body) != (task.title, task.body) or title_hit or body_hit
        changed = replace(
            task,
            title=title,
            body=body,
            labels=labels,
            pii_redacted=title_hit or body_hit,
            updated_at=self._clock.now(),
        )
        saved = await self._store.save(actor.user_id, changed, edit.version)
        await self._event(actor, saved, EventKind.EDITED)
        if saved.pii_redacted:
            await self._event(actor, saved, EventKind.PII_REDACTED)
        if is_text_change and self._hooks is not None:
            await self._hooks.on_text_changed(actor, saved)
        return await self.get(actor, task_id)

    async def delete(self, actor: Actor, task_id: uuid.UUID, version: int) -> None:
        task = await self.get(actor, task_id)
        if task.active_run_id is not None:
            raise ValidationFailed("Stop the card's run (kill switch) before deleting it.")
        await self._store.delete(actor.user_id, task_id, version, self._clock.now())

    async def move(self, actor: Actor, task_id: uuid.UUID, move: MoveRequest) -> TaskRecord:
        task = await self.get(actor, task_id)
        if task.version != move.version:
            # Checked first so a stale board never sees a misleading 422 or 403.
            raise _conflict(task)
        check = check_user_move(
            task, move.to_column, is_maintainer=actor.is_maintainer, reason=move.reason
        )
        if check.needs_plan_approval:
            await self._require_approved_plan(actor, task_id)
        rank = await place_at(self._store, actor.user_id, move.to_column, task_id, move.rank)
        moved = _into(task, move.to_column, rank, move.reason, self._clock.now())
        saved = await self._store.save(actor.user_id, moved, move.version)
        if check.is_reorder:
            return saved
        await self._event(actor, saved, EventKind.MOVED, from_column=task.column)
        if self._hooks is not None:
            await self._hooks.on_moved(actor, saved, task.column, reason=move.reason)
        return await self.get(actor, task_id)

    async def system_move(
        self,
        actor: Actor,
        task_id: uuid.UUID,
        target: Column,
        *,
        reason: str | None = None,
        run_id: uuid.UUID | None = None,
        change: Callable[[TaskRecord], TaskRecord] | None = None,
        rank: str | None = None,
    ) -> TaskRecord:
        """Triage, runs and the kill switch. `actor` is the system or agent acting for the
        account that triggered the work."""
        task = await self.get(actor, task_id)
        check_system_move(task, target)
        if target is Column.READY:
            await self._require_approved_plan(actor, task_id)
        if rank is None:
            placed = await place_last(self._store, actor.user_id, target, task_id)
        else:
            placed = await place_at(self._store, actor.user_id, target, task_id, rank)
        moved = _into(task, target, placed, reason, self._clock.now())
        if change is not None:
            moved = change(moved)
        saved = await self._store.save(actor.user_id, moved, task.version)
        await self._event(actor, saved, EventKind.MOVED, from_column=task.column, run_id=run_id)
        return saved

    async def system_edit(
        self, actor: Actor, task_id: uuid.UUID, change: Callable[[TaskRecord], TaskRecord]
    ) -> TaskRecord | None:
        """A system change to the card as it is now (PR sync: `pr_url`, labels), saved against
        the version just read, with an `edited` event. No hooks run. None when `change` leaves
        the card as it was. Raises `Conflict` when the card changed meanwhile."""
        task = await self.get(actor, task_id)
        changed = change(task)
        if changed == task:
            return None
        saved = await self._store.save(
            actor.user_id, replace(changed, updated_at=self._clock.now()), task.version
        )
        await self._event(actor, saved, EventKind.EDITED)
        return saved

    async def return_to_backlog(self, actor: Actor, task_id: uuid.UUID) -> TaskRecord:
        """After an accepted chat edit on a card past triage (`edit_returns_to_backlog`)."""
        task = await self.get(actor, task_id)
        if not edit_returns_to_backlog(task):
            raise IllegalTransition("This card is triaged again where it is.")
        placed = await place_last(self._store, actor.user_id, Column.BACKLOG, task_id)
        moved = _into(task, Column.BACKLOG, placed, None, self._clock.now())
        saved = await self._store.save(actor.user_id, moved, task.version)
        await self._event(actor, saved, EventKind.MOVED, from_column=task.column)
        return saved

    async def _require_approved_plan(self, actor: Actor, task_id: uuid.UUID) -> None:
        plan = await self._store.latest_plan(actor.user_id, task_id)
        if plan is None or not plan.is_approved:
            raise PlanNotApproved("Approve the tests and the plan first.")

    async def record(
        self, actor: Actor, task: TaskRecord, kind: EventKind, run_id: uuid.UUID | None = None
    ) -> None:
        await self._event(actor, task, kind, run_id=run_id)

    async def _event(
        self,
        actor: Actor,
        task: TaskRecord,
        kind: EventKind,
        *,
        from_column: Column | None = None,
        run_id: uuid.UUID | None = None,
    ) -> None:
        event = TaskEventRecord(
            id=self._ids.new_id(),
            task_id=task.id,
            at=self._clock.now(),
            kind=kind,
            actor_kind=actor.kind,
            actor_user_id=actor.user_id,
            from_column=from_column,
            to_column=task.column if kind is EventKind.MOVED else None,
            run_id=run_id,
        )
        await self._store.add_event(actor.user_id, event)


def _into(
    task: TaskRecord, target: Column, rank: str, reason: str | None, now: datetime
) -> TaskRecord:
    moved = replace(task, column=target, rank=rank, updated_at=now)
    if target is Column.BLOCKED and task.column is not Column.BLOCKED:
        clean = (reason or "").strip()[:1000] or None
        return replace(moved, blocked_from=task.column, blocked_reason=clean)
    if task.column is Column.BLOCKED and target is not Column.BLOCKED:
        return replace(moved, blocked_from=None, blocked_reason=None)
    return moved


def _check_labels(actor: Actor, labels: tuple[str, ...], existing: tuple[str, ...]) -> None:
    added = set(labels) - set(existing)
    removed = set(existing) - set(labels)
    touched = added | removed
    if not actor.is_maintainer and any(
        label.startswith(MAINTAINER_LABEL_PREFIXES) for label in touched
    ):
        raise Forbidden("Only a maintainer can change these labels.")


def _conflict(task: TaskRecord) -> Conflict:
    return Conflict("The card changed elsewhere.", current_version=task.version)
