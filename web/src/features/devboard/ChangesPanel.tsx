import { useMemo } from "react";
import type { TaskDiff } from "./api";
import styles from "./DevBoard.module.css";
import { FileDiffView } from "./FileDiffView";
import { parseUnifiedDiff } from "./unifiedDiff";

export interface ChangesPanelProps {
  diff: TaskDiff | null | undefined;
  loading: boolean;
  error?: string | undefined;
  downloading: boolean;
  downloadError?: string | undefined;
  onDownloadPatch: () => void;
}

const SHORT_SHA = 7;
/** Small diffs open every file; larger ones start collapsed so the page stays scannable. */
const OPEN_ALL_UP_TO = 3;

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** The agent branch: where it starts, how big it is, the diff per file, and the patch. */
export function ChangesPanel({
  diff,
  loading,
  error,
  downloading,
  downloadError,
  onDownloadPatch,
}: ChangesPanelProps) {
  const files = useMemo(() => (diff ? parseUnifiedDiff(diff.diff) : []), [diff]);
  return (
    <section className={styles.section} aria-labelledby="changes-title">
      <h2 id="changes-title">Changes</h2>
      {loading ? (
        <p className={styles.hint}>Loading the changes…</p>
      ) : error ? (
        <p className={styles.alert} role="alert">
          {error}
        </p>
      ) : !diff ? (
        <p className={styles.hint}>No changes published yet.</p>
      ) : (
        <>
          <dl className={styles.facts}>
            <dt>Branch</dt>
            <dd>
              <code className={styles.mono}>{diff.branch}</code>
            </dd>
            <dt>Base</dt>
            <dd>
              <code className={styles.mono} title={diff.base_sha}>
                {diff.base_sha.slice(0, SHORT_SHA)}
              </code>
            </dd>
            <dt>Size</dt>
            <dd>
              {plural(diff.files_changed, "file changed", "files changed")},{" "}
              <span className={styles.statAdded}>
                <span className="visually-hidden">Lines added: </span>
                <span>+{diff.insertions}</span>
              </span>{" "}
              <span className={styles.statRemoved}>
                <span className="visually-hidden">Lines removed: </span>
                <span>−{diff.deletions}</span>
              </span>
            </dd>
          </dl>
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.secondary}
              onClick={onDownloadPatch}
              disabled={downloading}
              aria-describedby="patch-hint"
            >
              {downloading ? "Downloading…" : "Download patch"}
            </button>
          </div>
          <p id="patch-hint" className={styles.hint}>
            Apply it in your own checkout with <code className={styles.mono}>git am</code>.
          </p>
          {downloadError ? (
            <p className={styles.alert} role="alert">
              {downloadError}
            </p>
          ) : null}
          {diff.truncated ? (
            <p className={styles.notice}>
              This diff is too large to show in full, so it is cut short. Download the patch to see
              every change.
            </p>
          ) : null}
          {files.length ? (
            <div className={styles.patchFiles}>
              {files.map((file) => (
                <FileDiffView
                  key={`${file.oldPath ?? ""}→${file.path}`}
                  file={file}
                  open={files.length <= OPEN_ALL_UP_TO}
                />
              ))}
            </div>
          ) : (
            <p className={styles.hint}>The diff is empty.</p>
          )}
        </>
      )}
    </section>
  );
}
