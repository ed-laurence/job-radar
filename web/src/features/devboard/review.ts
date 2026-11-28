import type { ReviewFinding, TaskReview } from "./api";

/** The label the review policy puts on a card whose round-2 review still has blockers. */
export const NEEDS_DECISION_LABEL = "needs-decision";

export const VERDICT_LABEL: Record<TaskReview["verdict"], string> = {
  approved: "Approved",
  changes_requested: "Changes requested",
  needs_decision: "Needs decision",
};

/** BLOCKERs first; the reviewer's order is kept within each severity. */
export function sortFindings(findings: readonly ReviewFinding[]): ReviewFinding[] {
  const rank = (f: ReviewFinding) => (f.severity === "BLOCKER" ? 0 : 1);
  return [...findings].sort((a, b) => rank(a) - rank(b));
}

/** Every citation the finding carries: rubric id, test id and repro, in that order. */
export function citations(finding: ReviewFinding): string[] {
  return [
    finding.rubric_id ? `Rubric ${finding.rubric_id}` : null,
    finding.test_id ? `Test ${finding.test_id}` : null,
    finding.repro ? `Repro: ${finding.repro}` : null,
  ].filter((c): c is string => c !== null);
}

export function location(finding: ReviewFinding): string {
  return finding.line === undefined ? finding.path : `${finding.path}:${finding.line}`;
}
