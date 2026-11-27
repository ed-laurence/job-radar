"use client";

import { useEffect, useId, useRef } from "react";
import styles from "./ConfirmPanel.module.css";

export interface ConfirmPanelProps {
  title: string;
  description: string;
  confirmLabel: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** An inline confirmation: focus moves to the confirm button, Escape cancels. */
export function ConfirmPanel({
  title,
  description,
  confirmLabel,
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmPanelProps) {
  const id = useId();
  const confirmRef = useRef<HTMLButtonElement>(null);
  useEffect(() => confirmRef.current?.focus(), []);

  return (
    <div
      role="alertdialog"
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-desc`}
      className={styles.confirm}
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
      }}
    >
      <h3 id={`${id}-title`} className={styles.title}>
        {title}
      </h3>
      <p id={`${id}-desc`} className={styles.text}>
        {description}
      </p>
      <div className={styles.actions}>
        <button
          ref={confirmRef}
          type="button"
          className={styles.danger}
          disabled={busy}
          onClick={onConfirm}
        >
          {confirmLabel}
        </button>
        <button type="button" className={styles.button} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
