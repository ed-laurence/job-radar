import styles from "./States.module.css";

export interface ErrorStateProps {
  title?: string;
  description?: string;
  /** Shown so a user can quote it; the only diagnostic the API exposes (SEC-07). */
  correlationId?: string | undefined;
  onRetry?: () => void;
}

export function ErrorState({
  title = "Something went wrong",
  description,
  correlationId,
  onRetry,
}: ErrorStateProps) {
  return (
    <div className={`${styles.state} ${styles.error}`} role="alert">
      <h2 className={styles.title}>{title}</h2>
      {description ? <p className={styles.text}>{description}</p> : null}
      {correlationId ? <p className={styles.reference}>Reference: {correlationId}</p> : null}
      {onRetry ? (
        <button type="button" className={styles.button} onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}
