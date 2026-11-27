# Task chat (dev board, streamed)

`/api/tasks/{id}/chat` is a conversation with Claude about one dev-board card. Claude can read the card and propose an edit; the person accepts or rejects it. Spec tag "Task chat".

## Who and what
- **Rights:** the same accounts that can edit the card (`updateTask`): every allowlisted account, either role. An account that isn't on the allowlist gets 403, as everywhere.
- **Per account:** the board is shared, but each account has its own conversation and proposals about a card (`task_chat_messages`, `task_chat_proposals`: user-owned, RLS `FORCE`d). The tester never sees the owner's chat, and can't accept or reject the owner's proposals (404).
- **Rate limits:** sending a message and accepting a proposal use the `ai` tier. History, clear and reject use `default`.

## Sending a message
`POST /tasks/{id}/chat` (`sendTaskChatMessage`) with `{content}` (1 to 4000 characters).
- The message passes `ContentGuard.inspect_input` before anything is saved. A blocked message is 422 `guard-blocked` and isn't stored.
- One reply per card per account at a time (409).
- The reply streams as SSE (`TaskChatStreamEvent`): one `step` per tool call, then `segment`s (`part` = `chat`), any `retract`, and `done` (`result_id` = the stored assistant message) or `error`. Steps have `agent` = `task-chat`, `tool` = the tool name, and `run_id` = the reply's message id. Every step goes through the step redactor and every segment through the segment filter.
- Only the filtered reply is stored. If the stream fails, no assistant message is saved.

## What the model sees (feature `task_chat`, Claude Sonnet, fallback Opus)
- **System prompt (trusted):** the board's scope rules. Frontend and API work only; infrastructure, CI, auth, guards, migrations, secrets, new env vars, dependencies, GCP services and the agent harness are out of scope. It says it can only read this card and propose edits.
- **Instructions:** the person's message.
- **Data blocks (untrusted, delimited):** the card (title, column, description, written acceptance criteria, labels), the triage summary, a plan summary, and the last 40 turns.
- **Tools** (a manual tool-use loop through the LLM adapter, ADR-0004, ADR-0009). None takes a card id, so no other card is reachable, nor the repo or running agents:
  - `get_task`, `get_triage`, `get_plan` are read-only and return the full card, triage or plan.
  - `propose_task_edit({title, description, acceptance_criteria, reason})`: `null` leaves a field as it is. It stores a **pending proposal** with field diffs and never edits the card. One proposal per reply; a second one, an empty or no-op proposal, or one over the size limits comes back to the model as a tool error and shows a "Couldn't propose the edit" step.
- At most 5 model turns per reply. Tool results pass the input guard before the next turn, and each turn's text passes the output guard (fail closed).

## Description and acceptance criteria
A card has one body. In chat it's two fields: the **description**, and the **written acceptance criteria**, which are the bullets under a `## Acceptance criteria` heading (or `Acceptance criteria:`) at the end of the body. The section runs to the end of the body. On accept, the body is rebuilt as the description, a blank line, `## Acceptance criteria`, then one `- ` bullet per criterion. Triage reads the body and turns the written criteria into executable test ids.

Proposed text is PII-redacted (SDP) when it's proposed, as on create and edit, so diffs never hold PII.

## Proposals
`TaskChatProposal`: `status` (`pending`, `accepted` or `rejected`), `reason`, `changes` (only the fields that change, each with `before` and `after`), `base_version`, `created_at` and `decided_at`. History (`listTaskChatMessages`) includes each assistant message's proposal.

### Accept
`POST /tasks/{id}/chat/proposals/{proposalId}/accept` with `{version}` (`acceptTaskChatProposal`). It returns `{proposal, task, returned_to_backlog, note?}`. The checks run in this order:
1. The proposal must exist for this account (404) and still be pending (409).
2. No agent run may be active on the card: 409, with no `current_version` and a detail saying to wait for the run or stop it.
3. `version` must match the card's: otherwise 409 `version-conflict` with `current_version`.
4. A `done` card can't be edited: 422 `illegal-transition`.
5. Every field the proposal changes must still read as its `before`. If someone changed one since, it's 409 with `current_version`. Edits to other fields don't make it stale.

The edit then goes through the normal task update: PII redaction, an `edited` event (and `pii_redacted` if anything was redacted) in the card's history, and the version bump.

**Triage and plan staleness (decision).** The state machine had no rule for editing a card after triage, so this one was added (`state_machine.edit_returns_to_backlog`):
- **Backlog, Triaged, Out of scope:** triage re-runs, as for any text edit.
- **Plan review, Ready, Blocked, Agent dev, Agent review, Changes requested, Human review** (when no run is active): the triage and the plan approval are stale. The latest plan's test and plan approvals are withdrawn, the card moves to `backlog` (a system `moved` event), triage re-runs, and a note is added to the chat. The response has `returned_to_backlog: true` and the same `note`. The card then needs a new plan review and approval before it can be Ready.
- **Done:** refused.

### Reject
`POST /tasks/{id}/chat/proposals/{proposalId}/reject` (`rejectTaskChatProposal`) marks it rejected. The card is unchanged. A proposal that was already decided is 409.

### Clear
`DELETE /tasks/{id}/chat` (`clearTaskChat`) deletes this account's conversation and proposals for the card. Accepted edits stay on the card and in its history.

## Offline
Under `LLM=replay` the conversation in `backend/tests/fixtures/llm/devboard/task_chat/` replays: the "Add a contacts search endpoint" card in Triaged, with the message in `tests/unit/devboard/chat_cards.py`. Any other conversation ends with an `error` event (`upstream-unavailable`: no offline recording).
