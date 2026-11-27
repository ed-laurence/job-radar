"use client";

import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type KeyboardEvent, useId, useRef, useState } from "react";
import { ConfirmPanel } from "@/components/ConfirmPanel";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { Markdown } from "@/components/Markdown";
import {
  currentVersionOf,
  describeError,
  describeProblem,
  type ErrorView,
  isApiError,
  type Problem,
} from "@/lib/api";
import { useStickToBottom } from "@/lib/useStickToBottom";
import { devboardKeys } from "./api";
import { ChatSegments } from "./ChatSegments";
import {
  TASK_CHAT_MAX_CHARS,
  type TaskChatMessage,
  type TaskChatProposal,
  useAcceptTaskChatProposal,
  useClearTaskChat,
  useRejectTaskChatProposal,
  useTaskChat,
} from "./chatApi";
import styles from "./DevBoard.module.css";
import type { Task } from "./model";
import { ProposalCard } from "./ProposalCard";
import { useTaskChatStream } from "./useTaskChatStream";

export interface TaskChatProps {
  task: Task;
  /** API base for the reply stream; the app default is `/api`. */
  apiBase?: string | undefined;
}

const RETURNED_TO_BACKLOG =
  "Edit accepted. The card moved back to Backlog so triage can run again, and its plan will " +
  "need a fresh review and approval.";

/** Pending proposals replaced by a later proposal that wasn't rejected. */
function supersededIds(messages: readonly TaskChatMessage[]): ReadonlySet<string> {
  const ids = new Set<string>();
  let later = false;
  for (let i = messages.length - 1; i >= 0; i--) {
    const p = messages[i]?.proposal;
    if (!p) continue;
    if (later && p.status === "pending") ids.add(p.id);
    if (p.status !== "rejected") later = true;
  }
  return ids;
}

/** What a failed accept means for the person, built only from the problem details (SEC-07). */
function acceptFailure(error: unknown): ErrorView {
  if (isApiError(error) && error.status === 409) {
    if (currentVersionOf(error) !== undefined) {
      return {
        title: "This task changed since you opened it",
        description:
          "It has been reloaded. Check the proposed edit still makes sense, then accept it " +
          "again. If it fails again, the fields it changes were edited since: ask for a new edit.",
        correlationId: error.correlationId,
        fieldErrors: [],
      };
    }
    return {
      title: "Couldn't accept the edit yet",
      description:
        error.problem.detail ??
        "An agent run is active on this card. Wait for it to finish, or stop it, then accept again.",
      correlationId: error.correlationId,
      fieldErrors: [],
    };
  }
  return describeError(error, "Couldn't accept the edit");
}

function isClientProblem(problem: Problem): boolean {
  return problem.status >= 400 && problem.status < 500;
}

/** "Discuss this task": a chat with Claude about one card, with inline proposed edits. */
export function TaskChat({ task, apiBase }: TaskChatProps) {
  const id = useId();
  const taskId = task.id;
  const queryClient = useQueryClient();
  const history = useTaskChat(taskId);
  const clear = useClearTaskChat(taskId);
  const accept = useAcceptTaskChatProposal(taskId);
  const reject = useRejectTaskChatProposal(taskId);
  const stream = useTaskChatStream(apiBase);
  const windowRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  /** Which send is current, so a late-settling stopped reply is ignored. */
  const turnRef = useRef(0);
  useStickToBottom(windowRef);

  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState("");
  const [status, setStatus] = useState("");
  const [replyError, setReplyError] = useState<Problem | undefined>();
  const [confirmingClear, setConfirmingClear] = useState(false);
  const [failure, setFailure] = useState<{ proposalId: string; view: ErrorView } | null>(null);
  const streaming = stream.phase === "streaming";
  const deciding = accept.isPending || reject.isPending;

  const send = async (event?: FormEvent) => {
    event?.preventDefault();
    const content = draft.trim();
    if (!content || streaming) return;
    const turn = ++turnRef.current;
    setPending(content);
    setDraft("");
    setStatus("");
    setReplyError(undefined);
    inputRef.current?.focus();
    const outcome = await stream.start(taskId, content);
    // A stopped reply may settle after the next message started; it must not touch that one.
    if (turn !== turnRef.current) return;
    if (outcome.kind === "done") setStatus("Claude replied.");
    if (outcome.kind === "aborted") setStatus("Reply stopped.");
    if (outcome.kind === "error" || outcome.kind === "failed") {
      setReplyError(outcome.problem);
      // A refused message (guard, conflict, too long) was never stored: give it back to edit.
      if (outcome.kind === "error" && isClientProblem(outcome.problem)) {
        setDraft((d) => d || content);
      }
    }
    await queryClient.invalidateQueries({ queryKey: devboardKeys.chat(taskId) });
    if (turn === turnRef.current) stream.reset();
  };

  const stop = () => {
    turnRef.current++;
    stream.stop();
    setStatus("Reply stopped.");
    // The message was saved before the reply started; the partial reply never is.
    void queryClient.invalidateQueries({ queryKey: devboardKeys.chat(taskId) });
    inputRef.current?.focus();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    void send();
  };

  const onAccept = (proposal: TaskChatProposal) => {
    setFailure(null);
    setStatus("");
    accept.mutate(
      { proposalId: proposal.id, version: task.version },
      {
        onSuccess: (result) =>
          setStatus(
            result.returned_to_backlog ? RETURNED_TO_BACKLOG : "Edit accepted. Triage runs again.",
          ),
        onError: (error) => setFailure({ proposalId: proposal.id, view: acceptFailure(error) }),
      },
    );
  };

  const onReject = (proposal: TaskChatProposal) => {
    setFailure(null);
    setStatus("");
    reject.mutate(proposal.id, {
      onSuccess: () => setStatus("Edit rejected. The card is unchanged."),
      onError: (error) =>
        setFailure({
          proposalId: proposal.id,
          view: describeError(error, "Couldn't reject the edit"),
        }),
    });
  };

  const messages = history.data ?? [];
  const superseded = supersededIds(messages);

  return (
    <section className={styles.section} aria-label="Task chat">
      <h2>Discuss this task</h2>
      <p className={styles.hint}>
        Ask Claude about this card. It can read the card, its triage and plan, and propose an edit
        you can accept or reject.
      </p>
      {/* A chat-style window that follows the newest reply while you're at the bottom. */}
      <div
        ref={windowRef}
        className={styles.chatWindow}
        role="log"
        aria-label="Conversation"
        aria-busy={streaming}
      >
        {history.isPending ? (
          <LoadingState label="Loading the conversation…" />
        ) : history.isError ? (
          <ErrorState
            {...describeError(history.error, "The conversation failed to load")}
            onRetry={() => void history.refetch()}
          />
        ) : messages.length === 0 && !streaming ? (
          <p className={styles.hint}>No messages yet.</p>
        ) : (
          <ul className={styles.messages}>
            {messages.map((m) => (
              <li key={m.id} className={m.author === "user" ? styles.messageUser : styles.message}>
                <span className={styles.author}>{m.author === "user" ? "You" : "Claude"}</span>
                {m.author === "user" ? (
                  <p className={styles.body}>{m.content}</p>
                ) : (
                  <Markdown>{m.content}</Markdown>
                )}
                {m.proposal ? (
                  <ProposalCard
                    proposal={m.proposal}
                    superseded={superseded.has(m.proposal.id)}
                    busy={deciding}
                    error={failure?.proposalId === m.proposal.id ? failure.view : undefined}
                    onAccept={() => m.proposal && onAccept(m.proposal)}
                    onReject={() => m.proposal && onReject(m.proposal)}
                  />
                ) : null}
              </li>
            ))}
            {streaming ? (
              <>
                <li className={styles.messageUser}>
                  <span className={styles.author}>You</span>
                  <p className={styles.body}>{pending}</p>
                </li>
                <li className={styles.message}>
                  <span className={styles.author}>Claude</span>
                  {stream.steps.length ? (
                    <ul className={styles.chatSteps} aria-label="Steps">
                      {stream.steps.map((s) => (
                        <li key={s.seq}>{s.summary}</li>
                      ))}
                    </ul>
                  ) : null}
                  <ChatSegments segments={stream.reply.segments} />
                </li>
              </>
            ) : null}
          </ul>
        )}
      </div>
      <p role="status" aria-live="polite" className={styles.hint}>
        {streaming ? "Claude is replying…" : status}
      </p>
      {replyError ? <ErrorState {...describeProblem(replyError, "The reply failed")} /> : null}
      <form className={styles.form} onSubmit={(e) => void send(e)}>
        <div className={styles.field}>
          <label htmlFor={`${id}-msg`}>Message</label>
          <textarea
            id={`${id}-msg`}
            ref={inputRef}
            className={styles.chatInput}
            maxLength={TASK_CHAT_MAX_CHARS}
            aria-describedby={`${id}-hint`}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <p id={`${id}-hint`} className={styles.hint}>
            Enter sends, Shift+Enter adds a new line. {draft.length} of {TASK_CHAT_MAX_CHARS}{" "}
            characters.
          </p>
        </div>
        <div className={styles.actions}>
          <button type="submit" className={styles.button} disabled={streaming || !draft.trim()}>
            Send
          </button>
          {streaming ? (
            <button type="button" className={styles.danger} onClick={stop}>
              Stop reply
            </button>
          ) : null}
          {messages.length > 0 && !streaming && !confirmingClear ? (
            <button
              type="button"
              className={styles.secondary}
              onClick={() => setConfirmingClear(true)}
            >
              Clear chat
            </button>
          ) : null}
        </div>
      </form>
      {confirmingClear ? (
        <ConfirmPanel
          title="Clear this conversation?"
          description="Your messages and Claude's proposals about this card are deleted. Accepted edits stay on the card."
          confirmLabel="Yes, clear"
          busy={clear.isPending}
          onCancel={() => setConfirmingClear(false)}
          onConfirm={() =>
            clear.mutate(undefined, {
              onSuccess: () => setStatus("Conversation cleared."),
              onSettled: () => setConfirmingClear(false),
            })
          }
        />
      ) : null}
      {clear.isError ? (
        <ErrorState {...describeError(clear.error, "Couldn't clear the conversation")} />
      ) : null}
    </section>
  );
}
