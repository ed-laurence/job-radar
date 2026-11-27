"use client";

import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { isApiError, labelFor } from "@/lib/api";
import { useRejectionPatterns } from "./api";
import styles from "./Dashboard.module.css";
import { RejectionTrends } from "./JobAnalytics";

function Patterns() {
  const patterns = useRejectionPatterns();
  if (patterns.isPending) return <LoadingState label="Loading rejection patterns…" />;
  if (patterns.isError) {
    return (
      <ErrorState
        title="Rejection patterns failed to load"
        correlationId={isApiError(patterns.error) ? patterns.error.correlationId : undefined}
        onRetry={() => void patterns.refetch()}
      />
    );
  }
  const d = patterns.data;
  const max = Math.max(1, ...d.categories.map((c) => c.count));
  return (
    <section className={styles.panel} aria-labelledby="feedback-title">
      <div className={styles.panelHead}>
        <h2 id="feedback-title" className={styles.caps}>
          Why you were turned down
        </h2>
        <span className={styles.sub}>
          {d.analysed} of {d.total} rejections analysed
        </span>
      </div>
      {d.categories.length ? (
        <dl className={styles.bars}>
          {d.categories.map((c) => (
            <div key={c.category} className={styles.barrow}>
              <dt>{labelFor("RejectionCategory", c.category)}</dt>
              <dd>
                <span className={styles.barTrack} aria-hidden="true">
                  <span className={styles.barFill} style={{ width: `${(c.count / max) * 100}%` }} />
                </span>
                <span className={styles.barValue}>{c.count}</span>
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className={styles.sub}>
          No reasons recorded yet. Paste the rejection message on a Rejected role to analyse it.
        </p>
      )}
      {d.lessons.length ? (
        <>
          <h3 className={styles.caps}>Lessons</h3>
          <ul className={styles.lessons}>
            {d.lessons.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}

/** The Feedback tab: rejection patterns and lessons, then rejection reasons over time. */
export function FeedbackPanel() {
  return (
    <>
      <Patterns />
      <RejectionTrends />
    </>
  );
}
