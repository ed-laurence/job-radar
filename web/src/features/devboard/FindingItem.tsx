import type { ReviewFinding } from "./api";
import styles from "./DevBoard.module.css";
import { citations, location } from "./review";

export interface FindingItemProps {
  finding: ReviewFinding;
}

/** One reviewer finding: severity, citation, where, what and how to fix it. */
export function FindingItem({ finding }: FindingItemProps) {
  const cited = citations(finding);
  const tone = finding.severity === "BLOCKER" ? styles.badgeDanger : styles.badgeNeutral;
  return (
    <li className={styles.finding}>
      <div className={styles.findingHeader}>
        <span className={[styles.badge, tone].join(" ")}>{finding.severity}</span>
        <code className={styles.mono}>{location(finding)}</code>
        {finding.downgraded ? <span className={styles.meta}>downgraded: no citation</span> : null}
      </div>
      {cited.length ? (
        <ul className={styles.citations} aria-label="Citation">
          {cited.map((citation) => (
            <li key={citation}>{citation}</li>
          ))}
        </ul>
      ) : finding.downgraded ? null : (
        <p className={styles.meta}>No citation.</p>
      )}
      <p className={styles.body}>{finding.evidence}</p>
      {finding.fix ? (
        <p className={styles.body}>
          <strong>Fix:</strong> {finding.fix}
        </p>
      ) : null}
    </li>
  );
}
