"use client";

import { type KeyboardEvent, type ReactNode, useEffect, useId, useRef } from "react";
import styles from "./DevBoard.module.css";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export interface ModalShellProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** A side drawer (forms) or a centred alert dialog (confirmations). */
  variant?: "drawer" | "alert";
}

/** A modal dialog: focus moves in on open, is trapped, and returns on close; Escape closes. */
export function ModalShell({ title, onClose, children, variant = "drawer" }: ModalShellProps) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const first = ref.current?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? ref.current)?.focus();
    return () => previous?.focus();
  }, []);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== "Tab" || !ref.current) return;
    const items = [...ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
    const first = items[0];
    const last = items.at(-1);
    if (!first || !last) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const backdrop = `${styles.backdrop} ${variant === "alert" ? styles.backdropCentered : ""}`;
  const content = (
    <>
      <h2 id={titleId}>{title}</h2>
      {children}
    </>
  );
  // Separate branches keep the role static, so the ARIA lint can check it.
  return (
    <div className={backdrop}>
      {variant === "alert" ? (
        <div
          ref={ref}
          role="alertdialog"
          aria-modal="true"
          aria-labelledby={titleId}
          tabIndex={-1}
          className={styles.dialog}
          onKeyDown={onKeyDown}
        >
          {content}
        </div>
      ) : (
        <div
          ref={ref}
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          tabIndex={-1}
          className={styles.drawer}
          onKeyDown={onKeyDown}
        >
          {content}
        </div>
      )}
    </div>
  );
}
