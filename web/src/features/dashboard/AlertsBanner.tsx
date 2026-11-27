"use client";

import { useAlerts } from "./api";
import styles from "./Dashboard.module.css";

/** The warm banner above the tiles: failed jobs, follow-ups due, quiet feeds. Hidden when empty. */
export function AlertsBanner() {
  const alerts = useAlerts();
  const items = alerts.data?.items ?? [];
  return (
    <section
      className={styles.alerts}
      aria-label="Alerts"
      aria-live="polite"
      hidden={!items.length}
    >
      {items.map((a) => (
        <p
          key={`${a.level}-${a.title}`}
          className={
            a.level === "error" ? `${styles.alertLine} ${styles.alertBad}` : styles.alertLine
          }
        >
          <b>{a.title}</b>
          {a.text}
        </p>
      ))}
    </section>
  );
}
