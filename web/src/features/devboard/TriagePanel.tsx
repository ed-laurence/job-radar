import type { TriageResult } from "./api";
import styles from "./DevBoard.module.css";

export interface TriagePanelProps {
  triage: TriageResult | null | undefined;
  loading: boolean;
}

export function TriagePanel({ triage, loading }: TriagePanelProps) {
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
          <h3>Acceptance criteria</h3>
          {triage.acceptance_criteria.length ? (
            <ul>
              {triage.acceptance_criteria.map((c) => (
                <li key={c.test_id}>
                  <span className={styles.mono}>{c.test_id}</span>: {c.expectation}
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.hint}>None.</p>
          )}
        </>
      )}
    </section>
  );
}
