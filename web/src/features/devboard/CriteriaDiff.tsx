import type { Schemas } from "@/lib/api";
import styles from "./DevBoard.module.css";
import { DiffSide } from "./DiffSide";
import { type CriterionDiff, diffCriteria, diffWords } from "./diff";

const TAGS: Record<CriterionDiff["kind"], string> = {
  same: "Unchanged",
  added: "Added",
  removed: "Removed",
  changed: "Changed",
};

/** Stable keys for list items that may repeat (two identical criteria). */
function withKeys(items: CriterionDiff[]): (CriterionDiff & { key: string })[] {
  const seen = new Map<string, number>();
  return items.map((item) => {
    const base =
      item.kind === "changed"
        ? `changed:${item.before}\n${item.after}`
        : `${item.kind}:${item.text}`;
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    return { ...item, key: `${base}#${n}` };
  });
}

export interface CriteriaDiffProps {
  change: Schemas["TaskCriteriaChange"];
}

/** The written acceptance criteria as unchanged, removed, changed and added items. */
export function CriteriaDiff({ change }: CriteriaDiffProps) {
  const items = withKeys(diffCriteria(change.before, change.after));
  return (
    <fieldset className={styles.diffField}>
      <legend className={styles.diffLegend}>Acceptance criteria</legend>
      {items.length === 0 ? (
        <p className={styles.hint}>No acceptance criteria.</p>
      ) : (
        <ul className={styles.criteria} aria-label="Acceptance criteria">
          {items.map((item) => (
            <li key={item.key} className={item.kind === "same" ? styles.criterionSame : undefined}>
              <span className={styles.diffTag}>{TAGS[item.kind]}: </span>
              {item.kind === "changed" ? (
                <>
                  <DiffSide parts={diffWords(item.before, item.after)} side="before" />
                  <span aria-hidden="true"> → </span>
                  <span className="visually-hidden"> becomes </span>
                  <DiffSide parts={diffWords(item.before, item.after)} side="after" />
                </>
              ) : item.kind === "added" ? (
                <ins className={styles.diffAdded}>{item.text}</ins>
              ) : item.kind === "removed" ? (
                <del className={styles.diffRemoved}>{item.text}</del>
              ) : (
                item.text
              )}
            </li>
          ))}
        </ul>
      )}
    </fieldset>
  );
}
