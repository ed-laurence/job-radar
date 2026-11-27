"use client";

import { useEffect } from "react";
import styles from "./Toast.module.css";

export interface ToastProps {
  /** Empty hides the toast; the live region stays mounted so screen readers announce changes. */
  message: string;
  onDismiss: () => void;
  /** Auto-dismiss after this many milliseconds (default 5 s); 0 keeps it until dismissed. */
  timeoutMs?: number;
}

/** A polite success toast, fixed at the bottom of the screen. */
export function Toast({ message, onDismiss, timeoutMs = 5000 }: ToastProps) {
  useEffect(() => {
    if (!message || !timeoutMs) return;
    const timer = window.setTimeout(onDismiss, timeoutMs);
    return () => window.clearTimeout(timer);
  }, [message, timeoutMs, onDismiss]);

  return (
    <div className={styles.region} role="status" aria-live="polite">
      {message ? (
        <div className={styles.toast}>
          <p>{message}</p>
          <button type="button" className={styles.dismiss} onClick={onDismiss}>
            Dismiss
          </button>
        </div>
      ) : null}
    </div>
  );
}
