"use client";

import { type ReactNode, useRef, useState } from "react";
import { ConfirmPanel } from "@/components/ConfirmPanel";
import { type ExperienceFields, ExperienceForm } from "@/components/ExperienceForm";
import { describeError, isNotFound, isVersionConflict } from "@/lib/api";
import { type ExperienceRecord, useUpdateExperience } from "./api";
import styles from "./Settings.module.css";

/** A worded failure for an inline alert region. */
export function errorNode(error: unknown, fallback: string): ReactNode {
  const e = describeError(error, fallback);
  return (
    <div className={styles.alert}>
      <p className={styles.strong}>{e.title}</p>
      {[e.description, ...e.fieldErrors].filter(Boolean).map((m) => (
        <p key={m}>{m}</p>
      ))}
      {e.correlationId ? <p>Reference: {e.correlationId}</p> : null}
    </div>
  );
}

export function conflictNode(message: string): ReactNode {
  return (
    <div className={styles.alert}>
      <p className={styles.strong}>This record changed elsewhere</p>
      <p>{message}</p>
    </div>
  );
}

export const DELETED_ELSEWHERE = "It was deleted elsewhere, so your edit wasn't saved.";

export interface ExperienceRecordEditorProps {
  record: ExperienceRecord;
  contexts: readonly string[];
  deleting: boolean;
  /** Reloads the list and returns the latest records (after a conflict). */
  refetch: () => Promise<readonly ExperienceRecord[] | undefined>;
  /** Announced politely by the section. */
  onNote: (text: string) => void;
  /** The record has gone (deleted elsewhere): the section shows why. */
  onGone: (message: ReactNode) => void;
  /** Delete was confirmed. */
  onDelete: () => void;
}

/**
 * One record, edited in place (job-seeker's .exprec): its requirement, ref and use count, its
 * rows, Save, and Delete behind a confirmation. Saves send the version the edit started from.
 */
export function ExperienceRecordEditor({
  record,
  contexts,
  deleting,
  refetch,
  onNote,
  onGone,
  onDelete,
}: ExperienceRecordEditorProps) {
  const update = useUpdateExperience();
  const [base, setBase] = useState(record.version);
  const [error, setError] = useState<ReactNode>(null);
  const [confirming, setConfirming] = useState(false);
  const deleteRef = useRef<HTMLButtonElement>(null);
  const { id, uses } = record;

  const save = (fields: ExperienceFields) => {
    setError(null);
    onNote("");
    update.mutate(
      {
        id,
        body: {
          ...fields,
          ...(record.role_id ? { role_id: record.role_id } : {}),
          version: base,
        },
      },
      {
        onSuccess: (saved) => {
          setBase(saved.version);
          onNote(`Saved ${saved.requirement}.`);
        },
        onError: async (failure) => {
          if (isNotFound(failure)) {
            onGone(conflictNode(DELETED_ELSEWHERE));
            void refetch();
            return;
          }
          if (!isVersionConflict(failure)) {
            setError(errorNode(failure, "Your edit wasn't saved"));
            return;
          }
          const latest = (await refetch())?.find((r) => r.id === id);
          if (!latest) {
            onGone(conflictNode(DELETED_ELSEWHERE));
            return;
          }
          setBase(latest.version);
          setError(
            conflictNode(
              "Someone saved a newer version. Your edits are still here: save again to replace it with yours, or reload the page to see theirs.",
            ),
          );
        },
      },
    );
  };

  const meta = (
    <>
      <span className={styles.recordMeta}>
        {`${record.ref}${uses ? ` · matched in ${uses} ad${uses === 1 ? "" : "s"}` : ""}`}
      </span>
      {uses >= 3 ? <span className={styles.chipWarn}>worth adding to your base CV</span> : null}
    </>
  );

  return (
    <ExperienceForm
      name={record.requirement}
      className={styles.exprec}
      initial={record}
      contexts={contexts}
      meta={meta}
      submitLabel="Save"
      busy={update.isPending}
      error={error}
      onSubmit={save}
      actions={
        <button
          ref={deleteRef}
          type="button"
          className={styles.linkDanger}
          disabled={deleting}
          onClick={() => setConfirming(true)}
        >
          {deleting ? "Deleting…" : "Delete"}
        </button>
      }
    >
      {confirming ? (
        <ConfirmPanel
          title={`Delete ${record.requirement}?`}
          description="Kits and evidence tests will stop using it."
          confirmLabel="Delete"
          onConfirm={() => {
            setConfirming(false);
            onDelete();
          }}
          onCancel={() => {
            setConfirming(false);
            deleteRef.current?.focus();
          }}
        />
      ) : null}
    </ExperienceForm>
  );
}
