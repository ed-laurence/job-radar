import styles from "./DevBoard.module.css";
import type { DiffPart } from "./diff";

export interface DiffSideProps {
  parts: DiffPart[];
  side: "before" | "after";
}

/** One side of a word diff: `<del>` marks words only in "before", `<ins>` words only in "after". */
export function DiffSide({ parts, side }: DiffSideProps) {
  const hidden = side === "before" ? "added" : "removed";
  let offset = 0;
  const keyed = parts.map((part) => {
    const key = `${part.kind}-${offset}`;
    offset += part.text.length;
    return { ...part, key };
  });
  return (
    <>
      {keyed
        .filter((p) => p.kind !== hidden)
        .map((p) =>
          p.kind === "same" ? (
            <span key={p.key}>{p.text}</span>
          ) : p.kind === "removed" ? (
            <del key={p.key} className={styles.diffRemoved}>
              {p.text}
            </del>
          ) : (
            <ins key={p.key} className={styles.diffAdded}>
              {p.text}
            </ins>
          ),
        )}
    </>
  );
}
