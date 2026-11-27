import type { Step } from "@/lib/sse";
import styles from "./StepList.module.css";

const KIND_LABELS: Record<Step["kind"], string> = {
  thought: "Thinking",
  tool_call: "Tool call",
  tool_result: "Result",
  message: "Message",
  handoff: "Handoff",
  lease: "Lease",
  status: "Status",
  finding: "Finding",
};

export interface StepListProps {
  label: string;
  steps: Step[];
}

/** An agent run's steps. They arrive already redacted; summaries are shown as plain text. */
export function StepList({ label, steps }: StepListProps) {
  return (
    <ol className={styles.steps} aria-label={label}>
      {steps.map((s) => (
        <li key={s.seq}>
          <span className={styles.stepKind}>
            {KIND_LABELS[s.kind]}
            {s.tool ? ` · ${s.tool}` : ""}
          </span>
          <p className={styles.stepSummary}>{s.summary}</p>
        </li>
      ))}
    </ol>
  );
}
