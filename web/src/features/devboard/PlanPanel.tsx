import { useId, useState } from "react";
import type { Plan } from "./api";
import styles from "./DevBoard.module.css";
import { uncoveredTestChanges } from "./testChanges";

export interface PlanPanelProps {
  plan: Plan | null | undefined;
  /** The card's labels; `modify-tests:` labels already allow some existing tests to change. */
  labels: readonly string[];
  loading: boolean;
  /** Shown only to a maintainer while the card is in Plan review and not yet approved. */
  canApprove: boolean;
  approving: boolean;
  error?: string | undefined;
  /** `allowTestChanges` is true when the maintainer allowed the plan's uncovered test edits. */
  onApprove: (allowTestChanges: boolean) => void;
}

const usd = new Intl.NumberFormat("en", { style: "currency", currency: "USD" });

export function PlanPanel({
  plan,
  labels,
  loading,
  canApprove,
  approving,
  error,
  onApprove,
}: PlanPanelProps) {
  const approved = plan ? plan.plan_approved && plan.tests_approved : false;
  // Consent is tied to the plan version it was given for, so a revised plan asks again.
  const [allowedVersion, setAllowedVersion] = useState<number | null>(null);
  const testsId = useId();
  const testsHintId = `${testsId}-hint`;
  const testsToChange = plan?.tests_to_change ?? [];
  const needsConsent = uncoveredTestChanges(testsToChange, labels).length > 0;
  const allowed = needsConsent && plan != null && allowedVersion === plan.version;
  const blocked = needsConsent && !allowed;
  return (
    <section className={styles.section} aria-labelledby="plan-title">
      <h2 id="plan-title">Plan</h2>
      {loading ? (
        <p className={styles.hint}>Loading the plan…</p>
      ) : !plan ? (
        <p className={styles.hint}>
          No plan yet. A maintainer moves the card to Plan review to start one.
        </p>
      ) : (
        <>
          <dl className={styles.facts}>
            <dt>Mode</dt>
            <dd>{plan.mode === "multi" ? "Multi-agent" : "Single agent"}</dd>
            <dt>Risk</dt>
            <dd>{plan.risk}</dd>
            <dt>Budget</dt>
            <dd>
              {usd.format(plan.budget.max_usd)}, {plan.budget.max_minutes} min
            </dd>
            <dt>Approval</dt>
            <dd>
              {approved
                ? `Approved${plan.approved_at ? ` ${new Date(plan.approved_at).toLocaleString()}` : ""}`
                : `Tests ${plan.tests_approved ? "approved" : "not approved"}, plan ${plan.plan_approved ? "approved" : "not approved"}`}
            </dd>
          </dl>
          <h3>Subtasks</h3>
          <ol>
            {plan.subtasks.map((s) => (
              <li key={s.id}>
                {s.title}{" "}
                <span className={styles.meta}>
                  ({s.agent}
                  {s.test_only ? ", tests only" : ""})
                </span>
                {s.lease_paths.length ? (
                  <div className={styles.meta}>
                    Leases: <span className={styles.mono}>{s.lease_paths.join(", ")}</span>
                  </div>
                ) : null}
                {s.depends_on?.length ? (
                  <div className={styles.meta}>Depends on: {s.depends_on.join(", ")}</div>
                ) : null}
              </li>
            ))}
          </ol>
          <h3 id={`${testsId}-criteria`}>Acceptance criteria</h3>
          {plan.acceptance_criteria.length ? (
            <ul aria-labelledby={`${testsId}-criteria`}>
              {plan.acceptance_criteria.map((c) => (
                <li key={c.test_id}>
                  <span className={styles.mono}>{c.test_id}</span>: {c.expectation}
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.hint}>No acceptance criteria yet.</p>
          )}
          <h3>Frozen acceptance tests</h3>
          {plan.acceptance_tests.length ? (
            <ul>
              {plan.acceptance_tests.map((t) => (
                <li key={t} className={styles.mono}>
                  {t}
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.hint}>None written yet.</p>
          )}
          {testsToChange.length ? (
            <>
              <h3 id={`${testsId}-title`}>Existing tests this plan changes</h3>
              <ul aria-labelledby={`${testsId}-title`}>
                {testsToChange.map((t) => (
                  <li key={t} className={styles.mono}>
                    {t}
                  </li>
                ))}
              </ul>
              <p id={testsHintId} className={styles.hint}>
                Existing tests are read-only for agents, so a passing run can't quietly weaken them.{" "}
                {needsConsent
                  ? "Approving lets the agent edit these files and adds a modify-tests: label to the card."
                  : "The card's modify-tests: label already lets the agent edit these files."}
              </p>
              {canApprove && !approved && needsConsent ? (
                <label className={styles.checkField}>
                  <input
                    type="checkbox"
                    checked={allowed}
                    aria-describedby={testsHintId}
                    onChange={(e) =>
                      setAllowedVersion(e.currentTarget.checked && plan ? plan.version : null)
                    }
                  />
                  Allow the agent to edit these tests
                </label>
              ) : null}
            </>
          ) : null}
          {error ? (
            <p className={styles.alert} role="alert">
              {error}
            </p>
          ) : null}
          {canApprove && !approved ? (
            <div className={styles.actions}>
              <button
                type="button"
                className={styles.button}
                onClick={() => onApprove(allowed)}
                disabled={approving || blocked}
                aria-describedby={blocked ? testsHintId : undefined}
              >
                Approve plan + tests
              </button>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
