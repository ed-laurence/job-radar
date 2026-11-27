"use client";

import { type FormEvent, useState } from "react";
import styles from "./DevBoard.module.css";
import { ModalShell } from "./ModalShell";

export interface ConfirmDialogProps {
  title: string;
  message: string;
  confirmLabel: string;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
  pending?: boolean;
  danger?: boolean;
  /** Shows a reason field; `required` makes it mandatory (moving to Blocked). */
  reason?: { label: string; required: boolean; maxLength: number };
  error?: string | undefined;
}

export function ConfirmDialog({
  title,
  message,
  confirmLabel,
  onConfirm,
  onCancel,
  pending = false,
  danger = false,
  reason,
  error,
}: ConfirmDialogProps) {
  const [text, setText] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onConfirm(text.trim());
  };
  return (
    <ModalShell title={title} onClose={onCancel} variant="alert">
      <form className={styles.form} onSubmit={submit}>
        <p className={styles.body}>{message}</p>
        {reason ? (
          <label className={styles.field}>
            {reason.label}
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              required={reason.required}
              maxLength={reason.maxLength}
            />
          </label>
        ) : null}
        {error ? (
          <p className={styles.alert} role="alert">
            {error}
          </p>
        ) : null}
        <div className={styles.actions}>
          <button type="button" className={styles.secondary} onClick={onCancel}>
            Cancel
          </button>
          <button
            type="submit"
            className={danger ? styles.danger : styles.button}
            disabled={pending || (reason?.required === true && text.trim() === "")}
          >
            {confirmLabel}
          </button>
        </div>
      </form>
    </ModalShell>
  );
}
