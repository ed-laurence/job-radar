import type { Schemas } from "@/lib/api";
import styles from "./DevBoard.module.css";
import { DiffSide } from "./DiffSide";
import { diffWords } from "./diff";

export interface TextDiffProps {
  label: string;
  change: Schemas["TaskTextChange"];
}

/** A proposed change to one text field, as labelled before and after lines. */
export function TextDiff({ label, change }: TextDiffProps) {
  const parts = diffWords(change.before, change.after);
  return (
    <fieldset className={styles.diffField}>
      <legend className={styles.diffLegend}>{label}</legend>
      <p className={styles.diffLine}>
        <span className={styles.diffTag}>Before: </span>
        {change.before ? <DiffSide parts={parts} side="before" /> : <em>(empty)</em>}
      </p>
      <p className={styles.diffLine}>
        <span className={styles.diffTag}>After: </span>
        {change.after ? <DiffSide parts={parts} side="after" /> : <em>(empty)</em>}
      </p>
    </fieldset>
  );
}
