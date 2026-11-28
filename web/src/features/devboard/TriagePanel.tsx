import Link from "next/link";
import { useId } from "react";
import type { TriageResult } from "./api";
import styles from "./DevBoard.module.css";
import { ROUTE_LABEL, SEVERITY_LABEL, VERIFICATION_LABEL } from "./triageLabels";

export interface TriagePanelProps {
  triage: TriageResult | null | undefined;
  loading: boolean;
}

export function TriagePanel({ triage, loading }: TriagePanelProps) {
  const refsId = useId();
  const pathsId = useId();
  return (
    <section className={styles.section} aria-labelledby="triage-title">
      <h2 id="triage-title">Triage</h2>
      {loading ? (
        <p className={styles.hint}>Loading triage…</p>
      ) : !triage ? (
        <p className={styles.hint}>
          Not triaged yet. Triage runs when a task is created or edited.
        </p>
      ) : (
        <>
          {!triage.in_scope ? (
            <p className={styles.alert}>
              <strong>Out of scope.</strong> {triage.out_of_scope_reason ?? "No reason given."}{" "}
              Rewrite the task, or handle it outside the board.
            </p>
          ) : null}
          <dl className={styles.facts}>
            <dt>Priority</dt>
            <dd>{triage.priority}</dd>
            <dt>Severity</dt>
            <dd>{SEVERITY_LABEL[triage.severity]}</dd>
            <dt>Impact</dt>
            <dd>{triage.impact}</dd>
            <dt>Route</dt>
            <dd>
              <strong>{ROUTE_LABEL[triage.route]}</strong>: {triage.route_reason}
            </dd>
            <dt>Type</dt>
            <dd>{triage.type}</dd>
            <dt>Size</dt>
            <dd>{triage.size}</dd>
            <dt>Risk</dt>
            <dd>{triage.risk}</dd>
            <dt>Areas</dt>
            <dd>{triage.areas.length ? triage.areas.join(", ") : "None"}</dd>
            <dt>Needs human design</dt>
            <dd>{triage.needs_human_design ? "Yes" : "No"}</dd>
            {triage.model ? (
              <>
                <dt>Model</dt>
                <dd>
                  {triage.model}
                  {triage.provider ? ` (${triage.provider})` : ""}
                </dd>
              </>
            ) : null}
          </dl>
          <h3>Verification</h3>
          <dl className={styles.facts}>
            <dt>Status</dt>
            <dd>{VERIFICATION_LABEL[triage.verification.status]}</dd>
            {triage.verification.duplicate_of ? (
              <>
                <dt>Duplicate of</dt>
                <dd>
                  <Link href={`/dev-board/${triage.verification.duplicate_of}`}>
                    Open the original card
                  </Link>
                </dd>
              </>
            ) : null}
            <dt>Evidence</dt>
            <dd className={styles.body}>{triage.verification.evidence}</dd>
          </dl>
          <h4 id={refsId}>Code references</h4>
          {triage.verification.code_refs.length ? (
            <ul aria-labelledby={refsId}>
              {triage.verification.code_refs.map((path) => (
                <li key={path}>
                  <code className={styles.mono}>{path}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.hint}>No code references.</p>
          )}
          <h4 id={pathsId}>Likely paths</h4>
          {triage.likely_paths?.length ? (
            <ul aria-labelledby={pathsId}>
              {triage.likely_paths.map((path) => (
                <li key={path}>
                  <code className={styles.mono}>{path}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.hint}>No likely paths.</p>
          )}
        </>
      )}
    </section>
  );
}
