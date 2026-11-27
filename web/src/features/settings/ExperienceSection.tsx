"use client";

import { type ReactNode, useRef, useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import {
  EMPTY_EXPERIENCE,
  type ExperienceFields,
  ExperienceForm,
} from "@/components/ExperienceForm";
import { LoadingState } from "@/components/LoadingState";
import { describeError, isNotFound, isVersionConflict } from "@/lib/api";
import { useCreateExperience, useDeleteExperience, useExperience } from "./api";
import { conflictNode, ExperienceRecordEditor, errorNode } from "./ExperienceEditor";
import styles from "./Settings.module.css";

/** Experience not on your CV: records the evidence check and kits treat as fact. */
export function ExperienceSection() {
  const list = useExperience();
  const create = useCreateExperience();
  const del = useDeleteExperience();
  const [addKey, setAddKey] = useState(0);
  const [addError, setAddError] = useState<ReactNode>(null);
  const [listError, setListError] = useState<ReactNode>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const sectionRef = useRef<HTMLElement>(null);

  const records = list.data?.items ?? [];
  const contexts = list.data?.contexts ?? [];
  const refetch = async () => (await list.refetch()).data?.items;

  const add = (fields: ExperienceFields) => {
    setAddError(null);
    setNote("");
    create.mutate(fields, {
      onSuccess: (saved) => {
        setAddKey((k) => k + 1);
        setNote(`Added ${saved.requirement}.`);
      },
      onError: (error) => setAddError(errorNode(error, "The record couldn't be added")),
    });
  };

  const remove = (id: string) => {
    const record = records.find((r) => r.id === id);
    if (!record) return;
    setListError(null);
    setNote("");
    setDeletingId(id);
    del.mutate(
      { id, version: record.version },
      {
        onSuccess: () => {
          setNote(`Deleted ${record.requirement}.`);
          // Its Delete button has gone: keep focus in the section.
          sectionRef.current?.focus();
        },
        onError: (error) =>
          setListError(
            isVersionConflict(error)
              ? conflictNode("It changed before it was deleted. Check the latest and delete again.")
              : isNotFound(error)
                ? null
                : errorNode(error, "The record couldn't be deleted"),
          ),
        onSettled: () => setDeletingId(null),
      },
    );
  };

  return (
    <section
      ref={sectionRef}
      id="experience"
      className={styles.section}
      aria-labelledby="experience-title"
      tabIndex={-1}
    >
      <h2 id="experience-title" className={styles.sectionTitle}>
        Experience not on your CV{list.data ? ` (${records.length})` : ""}
      </h2>
      <p className={styles.muted}>
        Things you've done that aren't on your CVs, mostly added with <b>I've done this</b> on an
        evidence-test gap. Like your extra skills, they're treated as fact: the evidence test can
        count them, and kits use them in letters and CV tweaks. If one keeps coming up, it probably
        belongs on your base CV.
      </p>
      {list.isPending ? (
        <LoadingState label="Loading your experience…" />
      ) : list.isError ? (
        <ErrorState
          {...describeError(list.error, "Your experience failed to load")}
          onRetry={() => void list.refetch()}
        />
      ) : (
        <>
          {listError ? (
            <div role="alert" className={styles.inlineAlert}>
              {listError}
            </div>
          ) : null}
          {records.length ? (
            <ul className={styles.records} aria-label="Your experience records">
              {records.map((r) => (
                <li key={r.id}>
                  <ExperienceRecordEditor
                    record={r}
                    contexts={contexts}
                    deleting={deletingId === r.id}
                    refetch={refetch}
                    onNote={setNote}
                    onGone={(message) => {
                      setNote("");
                      setListError(message);
                      sectionRef.current?.focus();
                    }}
                    onDelete={() => remove(r.id)}
                  />
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.muted}>None yet.</p>
          )}
          <ExperienceForm
            key={addKey}
            name="Add one"
            heading={<b>Add one</b>}
            className={`${styles.exprec} ${styles.exprecNew}`}
            initial={EMPTY_EXPERIENCE}
            contexts={contexts}
            requirementPlaceholder="e.g. dbt, stakeholder management at board level"
            submitLabel="Add"
            busy={create.isPending}
            error={addError}
            onSubmit={add}
            focusOnMount={addKey > 0}
          />
        </>
      )}
      <p className={styles.visuallyHidden} role="status" aria-live="polite">
        {note}
      </p>
    </section>
  );
}
