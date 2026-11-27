"use client";

import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useId, useState } from "react";
import { ConfirmPanel } from "@/components/ConfirmPanel";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { Markdown } from "@/components/Markdown";
import { useAnalytics } from "@/lib/AnalyticsProvider";
import { describeError, describeProblem } from "@/lib/api";
import { roleKeys, useChatMessages, useClearChat } from "./api";
import styles from "./Roles.module.css";
import { StreamedSegments } from "./StreamedSegments";
import { useGenerationStream } from "./useGenerationStream";

export interface ChatPanelProps {
  roleId: string;
}

/** Chat about one role. The reply streams in; the saved thread is reloaded once it ends. */
export function ChatPanel({ roleId }: ChatPanelProps) {
  const id = useId();
  const queryClient = useQueryClient();
  const { track } = useAnalytics();
  const history = useChatMessages(roleId);
  const clear = useClearChat(roleId);
  const stream = useGenerationStream();
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState("");
  const [confirmingClear, setConfirmingClear] = useState(false);
  const [notice, setNotice] = useState("");
  const streaming = stream.phase === "streaming";

  const send = async (event: FormEvent) => {
    event.preventDefault();
    const content = draft.trim();
    if (!content || streaming) return;
    setPending(content);
    setDraft("");
    setNotice("");
    const outcome = await stream.start(`/roles/${encodeURIComponent(roleId)}/chat/messages`, {
      content,
    });
    if (outcome.kind === "done") track("chat_sent");
    if (outcome.kind === "aborted") setNotice("Reply stopped.");
    await queryClient.invalidateQueries({ queryKey: roleKeys.chat(roleId) });
    if (outcome.kind === "done" || outcome.kind === "aborted") stream.reset();
  };

  const messages = history.data?.items ?? [];

  return (
    <section className={styles.panel} aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`}>Chat about this role</h2>
      {history.isPending ? (
        <LoadingState label="Loading conversation…" />
      ) : history.isError ? (
        <ErrorState
          {...describeError(history.error, "Conversation failed to load")}
          onRetry={() => void history.refetch()}
        />
      ) : (
        <div role="log" aria-label="Conversation" aria-busy={streaming}>
          {messages.length === 0 && !streaming ? (
            <p className={styles.muted}>No messages yet.</p>
          ) : (
            <ul className={styles.messages}>
              {messages.map((m) => (
                <li
                  key={m.id}
                  className={m.author === "user" ? styles.messageUser : styles.message}
                >
                  <span className={styles.author}>{m.author === "user" ? "You" : "Assistant"}</span>
                  {m.author === "user" ? <p>{m.content}</p> : <Markdown>{m.content}</Markdown>}
                </li>
              ))}
              {streaming ? (
                <>
                  <li className={styles.messageUser}>
                    <span className={styles.author}>You</span>
                    <p>{pending}</p>
                  </li>
                  <li className={styles.message}>
                    <span className={styles.author}>Assistant</span>
                    <StreamedSegments segments={stream.state.segments} />
                  </li>
                </>
              ) : null}
            </ul>
          )}
        </div>
      )}
      <p role="status" className={styles.muted}>
        {streaming ? "Assistant is replying…" : notice}
      </p>
      {stream.phase === "error" ? (
        <ErrorState {...describeProblem(stream.state.error, "The reply failed")} />
      ) : null}
      <form className={styles.form} onSubmit={(e) => void send(e)}>
        <div className={`${styles.field} ${styles.wide}`}>
          <label htmlFor={`${id}-msg`}>Message</label>
          <textarea
            id={`${id}-msg`}
            className={styles.textarea}
            maxLength={4000}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
        </div>
        <div className={`${styles.actions} ${styles.wide}`}>
          {streaming ? (
            <button type="button" className={styles.danger} onClick={stream.stop}>
              Stop reply
            </button>
          ) : (
            <button type="submit" className={styles.primary} disabled={!draft.trim()}>
              Send
            </button>
          )}
          {messages.length > 0 && !streaming && !confirmingClear ? (
            <button
              type="button"
              className={styles.button}
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
          description="Every message about this role is deleted."
          confirmLabel="Yes, clear"
          busy={clear.isPending}
          onCancel={() => setConfirmingClear(false)}
          onConfirm={() => clear.mutate(undefined, { onSettled: () => setConfirmingClear(false) })}
        />
      ) : null}
    </section>
  );
}
