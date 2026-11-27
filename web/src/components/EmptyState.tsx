import type { ReactNode } from "react";
import styles from "./States.module.css";

export interface EmptyStateProps {
  title: string;
  description?: string;
  action?: ReactNode;
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <section className={styles.state} aria-label={title}>
      <h2 className={styles.title}>{title}</h2>
      {description ? <p className={styles.text}>{description}</p> : null}
      {action}
    </section>
  );
}
