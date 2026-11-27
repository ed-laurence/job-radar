"use client";

import { useState } from "react";
import { isActiveRun, type Run } from "./api";
import styles from "./DevBoard.module.css";
import type { RunTreeNode } from "./tree";
import { useRunSteps } from "./useRunSteps";

export interface RunNodeProps {
  node: RunTreeNode;
  leasesByAgent: ReadonlyMap<string, readonly string[]>;
  apiBase?: string | undefined;
  onRunFinished: () => void;
}

const usd = new Intl.NumberFormat("en", { style: "currency", currency: "USD" });

const KIND_LABELS: Record<Run["kind"], string> = {
  triage: "Triage",
  plan: "Dev lead (plan)",
  test_writer: "Test writer",
  dev: "Writer",
  publish: "Publisher",
  review: "Reviewer",
};

function budgetText(run: Run): string | undefined {
  if (run.cost_usd === undefined && run.budget_usd === undefined) return undefined;
  const cost = usd.format(run.cost_usd ?? 0);
  if (run.budget_usd === undefined) return `${cost} used`;
  const pct = run.budget_usd > 0 ? Math.round(((run.cost_usd ?? 0) / run.budget_usd) * 100) : 0;
  return `${cost} of ${usd.format(run.budget_usd)} (${pct}%)`;
}

/** One run in the tree, with its steps streamed live over SSE while it is expanded. */
export function RunNode({ node, leasesByAgent, apiBase, onRunFinished }: RunNodeProps) {
  const { run, children } = node;
  const active = isActiveRun(run);
  const [open, setOpen] = useState(active);
  const steps = useRunSteps(run.id, { enabled: open, baseUrl: apiBase, onFinished: onRunFinished });
  const name = `${KIND_LABELS[run.kind]}${run.agent ? `: ${run.agent}` : ""}`;
  const leases = run.agent ? leasesByAgent.get(run.agent) : undefined;
  const budget = budgetText(run);
  const statusClass =
    run.status === "succeeded"
      ? styles.statusOk
      : run.status === "failed" || run.status === "killed" || run.status === "timed_out"
        ? styles.statusBad
        : "";
  const stepsId = `run-${run.id}-steps`;

  return (
    <li className={styles.node} aria-label={`${name}, ${run.status}`}>
      <div className={styles.nodeHeader}>
        <strong>{name}</strong>
        <span className={`${styles.status} ${statusClass}`}>
          Status: {run.status.replace("_", " ")}
        </span>
        {run.round !== undefined && run.round > 0 ? (
          <span className={styles.meta}>Round {run.round}</span>
        ) : null}
        {budget ? <span className={styles.meta}>Budget: {budget}</span> : null}
        {run.model ? <span className={styles.meta}>{run.model}</span> : null}
      </div>
      {leases?.length ? (
        <p className={styles.meta}>
          Leases: <span className={styles.mono}>{leases.join(", ")}</span>
        </p>
      ) : null}
      {run.outcome ? <p className={styles.body}>{run.outcome}</p> : null}
      <button
        type="button"
        className={styles.disclosure}
        aria-expanded={open}
        aria-controls={stepsId}
        onClick={() => setOpen((o) => !o)}
      >
        {open ? "Hide steps" : "Show steps"}
      </button>
      {open ? (
        <ol id={stepsId} className={styles.steps} role="log" aria-label={`Steps: ${name}`}>
          {steps.steps.map((s) => (
            <li key={s.seq}>
              <span className={styles.meta}>
                {s.kind.replace("_", " ")}
                {s.tool ? ` · ${s.tool}` : ""}
                {s.agent && s.agent !== run.agent ? ` · ${s.agent}` : ""}
              </span>{" "}
              {s.summary}
            </li>
          ))}
          {steps.steps.length === 0 && steps.status === "streaming" ? (
            <li className={styles.meta}>Waiting for steps…</li>
          ) : null}
          {steps.status === "error" ? (
            <li className={styles.meta}>
              Step stream stopped{steps.error?.title ? `: ${steps.error.title}` : ""}.
            </li>
          ) : null}
        </ol>
      ) : null}
      {children.length ? (
        <ul className={styles.subtree}>
          {children.map((child) => (
            <RunNode
              key={child.run.id}
              node={child}
              leasesByAgent={leasesByAgent}
              apiBase={apiBase}
              onRunFinished={onRunFinished}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}
