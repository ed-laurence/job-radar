import type { ReviewFinding, TaskReview } from "./api";
import styles from "./DevBoard.module.css";
import { FindingItem } from "./FindingItem";
import { sortFindings, VERDICT_LABEL } from "./review";

export interface ReviewPanelProps {
  review: TaskReview | null | undefined;
  loading: boolean;
  error?: string | undefined;
}

/** Verdict colours reinforce the text label; they never carry the meaning alone. */
const VERDICT_CLASS: Record<TaskReview["verdict"], string | undefined> = {
  approved: styles.badgeAccent,
  changes_requested: styles.badgeWarm,
  needs_decision: styles.badgeDanger,
};

function count(findings: readonly ReviewFinding[], severity: ReviewFinding["severity"]): number {
  return findings.filter((f) => f.severity === severity).length;
}

function findingKey(finding: ReviewFinding): string {
  return [finding.severity, finding.path, finding.line, finding.rubric_id, finding.evidence].join(
    "|",
  );
}

/** The latest agent review: round, verdict, summary and findings (BLOCKERs first). */
export function ReviewPanel({ review, loading, error }: ReviewPanelProps) {
  return (
    <section className={styles.section} aria-labelledby="review-title">
      <h2 id="review-title">Agent review</h2>
      {loading ? (
        <p className={styles.hint}>Loading the review…</p>
      ) : error ? (
        <p className={styles.alert} role="alert">
          {error}
        </p>
      ) : !review ? (
        <p className={styles.hint}>No agent review yet.</p>
      ) : (
        <>
          <div className={styles.findingHeader}>
            <span className={styles.meta}>Round {review.round}</span>
            <span className={[styles.badge, VERDICT_CLASS[review.verdict]].join(" ")}>
              {VERDICT_LABEL[review.verdict]}
            </span>
          </div>
          <p className={styles.body}>{review.summary}</p>
          {review.findings.length ? (
            <>
              <h3>
                Findings: {count(review.findings, "BLOCKER")} blocking,{" "}
                {count(review.findings, "NIT")} minor
              </h3>
              <ul className={styles.findings} aria-label="Findings">
                {sortFindings(review.findings).map((finding) => (
                  <FindingItem key={findingKey(finding)} finding={finding} />
                ))}
              </ul>
            </>
          ) : (
            <p className={styles.hint}>No findings.</p>
          )}
        </>
      )}
    </section>
  );
}
