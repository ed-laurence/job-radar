"use client";

import Link from "next/link";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { isApiError, labelFor } from "@/lib/api";
import { useOutreach } from "./api";
import styles from "./Dashboard.module.css";

/** The Outreach tab: people to message, best first; the full list is on the Contacts page. */
export function OutreachPanel() {
  const contacts = useOutreach();
  if (contacts.isPending) return <LoadingState label="Loading outreach…" />;
  if (contacts.isError) {
    return (
      <ErrorState
        title="Outreach failed to load"
        correlationId={isApiError(contacts.error) ? contacts.error.correlationId : undefined}
        onRetry={() => void contacts.refetch()}
      />
    );
  }
  const items = [...contacts.data.items].sort(
    (a, b) => (b.outreach_score ?? -1) - (a.outreach_score ?? -1),
  );
  return (
    <section className={styles.panel} aria-labelledby="outreach-title">
      <div className={styles.panelHead}>
        <h2 id="outreach-title" className={styles.caps}>
          Outreach
        </h2>
        <Link href="/contacts" className={styles.linkbtn}>
          All contacts
        </Link>
      </div>
      {items.length ? (
        <ul className={styles.rows}>
          {items.map((c) => (
            <li key={c.id}>
              <span className={`${styles.chip} ${styles.chipStatus}`}>
                {labelFor("ContactStatus", c.status)}
              </span>
              <b>{[c.first_name, c.last_name].filter(Boolean).join(" ") || "Unnamed contact"}</b>
              <span className={styles.sub}>
                {[c.position, c.company].filter(Boolean).join(", ")}
                {c.outreach_reason ? ` · ${c.outreach_reason}` : ""}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className={styles.sub}>
          No contacts yet. Import your connections on the Contacts page to get suggestions.
        </p>
      )}
    </section>
  );
}
