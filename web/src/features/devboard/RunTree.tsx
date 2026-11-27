"use client";

import { useMemo } from "react";
import type { Plan, Run } from "./api";
import styles from "./DevBoard.module.css";
import { RunNode } from "./RunNode";
import { buildRunTree, leasesByAgent } from "./tree";

export interface RunTreeProps {
  runs: Run[] | undefined;
  plan: Plan | null | undefined;
  loading: boolean;
  apiBase?: string | undefined;
  onRunFinished: () => void;
}

export function RunTree({ runs, plan, loading, apiBase, onRunFinished }: RunTreeProps) {
  const tree = useMemo(() => buildRunTree(runs ?? []), [runs]);
  const leases = useMemo(() => leasesByAgent(plan), [plan]);
  return (
    <section className={styles.section} aria-labelledby="runs-title">
      <h2 id="runs-title">Agent runs</h2>
      {loading ? (
        <p className={styles.hint}>Loading runs…</p>
      ) : tree.length === 0 ? (
        <p className={styles.hint}>No runs yet.</p>
      ) : (
        <ul className={styles.tree} aria-label="Run tree">
          {tree.map((node) => (
            <RunNode
              key={node.run.id}
              node={node}
              leasesByAgent={leases}
              apiBase={apiBase}
              onRunFinished={onRunFinished}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
