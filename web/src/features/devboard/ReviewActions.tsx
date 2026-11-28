"use client";

import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import styles from "./DevBoard.module.css";

export interface ReviewActionsProps {
  pending: boolean;
  error?: string | undefined;
  onMarkDone: () => void;
  /** Called with the trimmed notes, which become the blockers for the rework run. */
  onRequestChanges: (notes: string) => void;
}

/** The spec's `TaskMove.reason` limit. */
const MAX_NOTES = 1000;

/** The maintainer's decision on a card in Human review. Rendered only for maintainers. */
export function ReviewActions({
  pending,
  error,
  onMarkDone,
  onRequestChanges,
}: ReviewActionsProps) {
  const [requesting, setRequesting] = useState(false);
  const [notes, setNotes] = useState("");
  const notesRef = useRef<HTMLTextAreaElement>(null);
  const hintId = useId();

  // The form opens only on the user's own click, so moving focus into it is expected.
  useEffect(() => {
    if (requesting) notesRef.current?.focus();
  }, [requesting]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = notes.trim();
    if (trimmed) onRequestChanges(trimmed);
  };

  return (
    <section className={styles.section} aria-labelledby="decision-title">
      <h2 id="decision-title">Your review</h2>
      {requesting ? (
        <form className={styles.form} onSubmit={submit}>
          <label className={styles.field}>
            Notes for the agent (required)
            <textarea
              ref={notesRef}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              required
              maxLength={MAX_NOTES}
              aria-describedby={hintId}
            />
          </label>
          <p id={hintId} className={styles.hint}>
            The notes become the blockers for the next dev run.
          </p>
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.secondary}
              onClick={() => {
                setRequesting(false);
                setNotes("");
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              className={styles.button}
              disabled={pending || notes.trim() === ""}
            >
              Send back for changes
            </button>
          </div>
        </form>
      ) : (
        <div className={styles.actions}>
          <button type="button" className={styles.button} onClick={onMarkDone} disabled={pending}>
            Mark done
          </button>
          <button
            type="button"
            className={styles.secondary}
            onClick={() => setRequesting(true)}
            disabled={pending}
          >
            Request changes
          </button>
        </div>
      )}
      {error ? (
        <p className={styles.alert} role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}
