import type { Plan } from "./api";
import styles from "./DevBoard.module.css";

export interface PlanPanelProps {
  plan: Plan | null | undefined;
  loading: boolean;
  /** Shown only to a maintainer while the card is in Plan review and not yet approved. */
  canApprove: boolean;
  approving: boolean;
  error?: string | undefined;
  onApprove: () => void;
}

const usd = new Intl.NumberFormat("en", { style: "currency", currency: "USD" });

export function PlanPanel({
  plan,
  loading,
  canApprove,
  approving,
  error,
  onApprove,
}: PlanPanelProps) {
  const approved = plan ? plan.plan_approved && plan.tests_approved : false;
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
                onClick={onApprove}
                disabled={approving}
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
