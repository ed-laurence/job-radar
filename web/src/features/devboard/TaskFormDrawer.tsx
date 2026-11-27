"use client";

import { type FormEvent, useState } from "react";
import styles from "./DevBoard.module.css";
import { ModalShell } from "./ModalShell";

export interface TaskFormValues {
  title: string;
  body: string;
  labels: string[];
}

export interface TaskFormDrawerProps {
  mode: "create" | "edit";
  initial?: TaskFormValues;
  pending: boolean;
  error?: string | undefined;
  onSubmit: (values: TaskFormValues) => void;
  onClose: () => void;
}

const EMPTY: TaskFormValues = { title: "", body: "", labels: [] };

function parseLabels(text: string): string[] {
  return [
    ...new Set(
      text
        .split(",")
        .map((l) => l.trim())
        .filter(Boolean),
    ),
  ].slice(0, 20);
}

/** Create or edit a card. Task text is stored as data and shown as plain text, never as HTML. */
export function TaskFormDrawer({
  mode,
  initial = EMPTY,
  pending,
  error,
  onSubmit,
  onClose,
}: TaskFormDrawerProps) {
  const [title, setTitle] = useState(initial.title);
  const [body, setBody] = useState(initial.body);
  const [labels, setLabels] = useState(initial.labels.join(", "));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit({ title: title.trim(), body: body.trim(), labels: parseLabels(labels) });
  };

  return (
    <ModalShell title={mode === "create" ? "New task" : "Edit task"} onClose={onClose}>
      <form className={styles.form} onSubmit={submit}>
        <label className={styles.field}>
          Title
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            maxLength={200}
          />
        </label>
        <label className={styles.field}>
          Description
          <textarea value={body} onChange={(e) => setBody(e.target.value)} maxLength={8000} />
        </label>
        <label className={styles.field}>
          Labels (comma separated)
          <input value={labels} onChange={(e) => setLabels(e.target.value)} />
        </label>
        <p className={styles.hint}>
          Personal information is removed before the task is saved. Editing the title or description
          runs triage again.
        </p>
        {error ? (
          <p className={styles.alert} role="alert">
            {error}
          </p>
        ) : null}
        <div className={styles.actions}>
          <button type="submit" className={styles.button} disabled={pending || !title.trim()}>
            {mode === "create" ? "Create task" : "Save changes"}
          </button>
          <button type="button" className={styles.secondary} onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </ModalShell>
  );
}
