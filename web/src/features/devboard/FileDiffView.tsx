import styles from "./DevBoard.module.css";
import type { DiffLineKind, FileDiff } from "./unifiedDiff";

export interface FileDiffViewProps {
  file: FileDiff;
  open: boolean;
}

const LINE_CLASS: Record<DiffLineKind, string | undefined> = {
  hunk: styles.patchHunk,
  context: undefined,
  added: styles.patchAdded,
  removed: styles.patchRemoved,
  note: styles.patchNote,
};

const STATUS_LABEL: Record<FileDiff["status"], string> = {
  added: "added",
  deleted: "deleted",
  modified: "modified",
  renamed: "renamed",
};

/** One file of the branch diff, collapsible. Lines keep their +/- prefix; colour only reinforces it. */
export function FileDiffView({ file, open }: FileDiffViewProps) {
  return (
    <details className={styles.patchFile} open={open}>
      <summary className={styles.patchSummary}>
        <code className={styles.mono}>{file.path}</code>{" "}
        <span className={styles.meta}>
          {file.status === "renamed" && file.oldPath ? (
            <>
              renamed from <code className={styles.mono}>{file.oldPath}</code>
            </>
          ) : (
            STATUS_LABEL[file.status]
          )}
          {file.binary ? ", binary" : ""}
        </span>{" "}
        <span className={styles.meta}>
          <span className="visually-hidden">Lines added: </span>+{file.additions}{" "}
          <span className="visually-hidden">Lines removed: </span>−{file.deletions}
        </span>
      </summary>
      {file.binary ? (
        <p className={styles.hint}>Binary file; download the patch to see it.</p>
      ) : file.lines.length === 0 ? (
        <p className={styles.hint}>No content changes.</p>
      ) : (
        <pre className={styles.patchCode}>
          {/* A line has no identity beyond its position, and the list never reorders. */}
          {[...file.lines.entries()].map(([position, line]) => (
            <span
              key={position}
              data-kind={line.kind}
              className={[styles.patchLine, LINE_CLASS[line.kind]].filter(Boolean).join(" ")}
            >
              {line.text}
            </span>
          ))}
        </pre>
      )}
    </details>
  );
}
