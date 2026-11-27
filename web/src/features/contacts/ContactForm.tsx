"use client";

import { type FormEvent, useId, useState } from "react";
import { isApiError, isVersionConflict, type Schemas } from "@/lib/api";
import { type Contact, useCreateContact, useFetchContact, useUpdateContact } from "./api";
import styles from "./Contacts.module.css";
import {
  CLOSENESS,
  CLOSENESS_LABELS,
  type Closeness,
  type ContactStatus,
  fullName,
  STATUS_LABELS,
  STATUSES,
} from "./labels";

const TEXT_FIELDS = [
  { name: "last_name", label: "Last name", max: 100, type: "text" },
  { name: "email", label: "Email", max: 254, type: "email" },
  { name: "company", label: "Company", max: 200, type: "text" },
  { name: "position", label: "Position", max: 200, type: "text" },
  { name: "url", label: "Profile link", max: 2048, type: "url" },
] as const;

type Field = "first_name" | (typeof TEXT_FIELDS)[number]["name"] | "status" | "closeness" | "notes";
type Draft = Record<Field, string>;
const FIELDS: Field[] = [
  "first_name",
  ...TEXT_FIELDS.map((f) => f.name),
  "status",
  "closeness",
  "notes",
];
const FIELD_LABELS: Record<Field, string> = {
  first_name: "First name",
  last_name: "Last name",
  email: "Email",
  company: "Company",
  position: "Position",
  url: "Profile link",
  status: "Status",
  closeness: "Closeness",
  notes: "Notes",
};

function toDraft(c: Contact | undefined): Draft {
  return {
    first_name: c?.first_name ?? "",
    last_name: c?.last_name ?? "",
    email: c?.email ?? "",
    company: c?.company ?? "",
    position: c?.position ?? "",
    url: c?.url ?? "",
    status: c?.status ?? "new",
    closeness: c?.closeness ?? "",
    notes: c?.notes ?? "",
  };
}

/**
 * The API never takes nulls and rejects blank emails and links. A new contact gets only filled-in
 * fields; an edit may blank a plain text field to clear it.
 */
function fill(target: Partial<Schemas["ContactCreate"]>, draft: Partial<Draft>, clearing: boolean) {
  for (const [key, raw] of Object.entries(draft) as [Field, string][]) {
    const value = raw.trim();
    if (key === "status") {
      if (value) target.status = value as ContactStatus;
    } else if (key === "closeness") {
      if (value) target.closeness = value as Closeness;
    } else if (key === "email" || key === "url") {
      if (value) target[key] = value;
    } else if (value || (clearing && key !== "first_name")) {
      target[key] = value;
    }
  }
}

export interface ContactFormProps {
  /** Absent for a new contact. */
  contact?: Contact;
  onDone: () => void;
}

export function ContactForm({ contact, onDone }: ContactFormProps) {
  const id = useId();
  const create = useCreateContact();
  const update = useUpdateContact();
  const fetchContact = useFetchContact();
  const [draft, setDraft] = useState<Draft>(() => toDraft(contact));
  const [conflict, setConflict] = useState<Contact | null>(null);
  const [error, setError] = useState<string | null>(null);
  const base = toDraft(contact);
  const busy = create.isPending || update.isPending;
  const name = contact ? fullName(contact) : "";

  const set = (field: Field) => (value: string) => setDraft((d) => ({ ...d, [field]: value }));

  const showError = (e: unknown) => {
    if (isApiError(e)) {
      const fields = (e.problem.errors ?? []).map((x) => x.message);
      setError([e.problem.title, e.problem.detail, ...fields].filter(Boolean).join(". "));
    } else {
      setError("The contact didn't save. Check your connection and try again.");
    }
  };

  const save = (version: number) => {
    setError(null);
    if (!contact) {
      const body: Schemas["ContactCreate"] = { first_name: draft.first_name.trim() };
      fill(body, draft, false);
      create.mutate(body, { onSuccess: onDone, onError: showError });
      return;
    }
    const changed: Partial<Draft> = {};
    for (const f of FIELDS) if (draft[f].trim() !== base[f].trim()) changed[f] = draft[f];
    if (Object.keys(changed).length === 0) {
      onDone();
      return;
    }
    const body: Schemas["ContactUpdate"] = { version };
    fill(body, changed, true);
    update.mutate(
      { id: contact.id, body },
      {
        onSuccess: onDone,
        onError: async (e) => {
          if (!isVersionConflict(e)) {
            showError(e);
            return;
          }
          try {
            setConflict(await fetchContact(contact.id));
          } catch (fetchError) {
            showError(fetchError);
          }
        },
      },
    );
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save(conflict?.version ?? contact?.version ?? 1);
  };

  const theirs = conflict ? toDraft(conflict) : null;
  const otherChanges = theirs ? FIELDS.filter((f) => theirs[f] !== base[f]) : [];
  const formName = contact ? `Edit ${name}` : "New contact";

  return (
    <form className={`${styles.panel} ${styles.form}`} aria-label={formName} onSubmit={submit}>
      <h2 className={styles.wide}>{formName}</h2>
      <div className={styles.field}>
        <label htmlFor={`${id}-first`}>
          First name <span>(required)</span>
        </label>
        <input
          id={`${id}-first`}
          className={styles.input}
          required
          maxLength={100}
          value={draft.first_name}
          onChange={(e) => set("first_name")(e.target.value)}
        />
      </div>
      {TEXT_FIELDS.map((f) => (
        <div key={f.name} className={styles.field}>
          <label htmlFor={`${id}-${f.name}`}>{f.label}</label>
          <input
            id={`${id}-${f.name}`}
            className={styles.input}
            type={f.type}
            maxLength={f.max}
            value={draft[f.name]}
            onChange={(e) => set(f.name)(e.target.value)}
          />
        </div>
      ))}
      <div className={styles.field}>
        <label htmlFor={`${id}-status`}>Status</label>
        <select
          id={`${id}-status`}
          className={styles.select}
          value={draft.status}
          onChange={(e) => set("status")(e.target.value)}
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {STATUS_LABELS[s]}
            </option>
          ))}
        </select>
      </div>
      <div className={styles.field}>
        <label htmlFor={`${id}-close`}>Closeness</label>
        <select
          id={`${id}-close`}
          className={styles.select}
          value={draft.closeness}
          onChange={(e) => set("closeness")(e.target.value)}
        >
          <option value="">Not set</option>
          {CLOSENESS.map((c) => (
            <option key={c} value={c}>
              {CLOSENESS_LABELS[c]}
            </option>
          ))}
        </select>
      </div>
      <div className={`${styles.field} ${styles.wide}`}>
        <label htmlFor={`${id}-notes`}>Notes</label>
        <textarea
          id={`${id}-notes`}
          className={styles.textarea}
          maxLength={4000}
          value={draft.notes}
          onChange={(e) => set("notes")(e.target.value)}
        />
      </div>
      {conflict && theirs ? (
        <div role="alert" className={`${styles.alert} ${styles.wide}`}>
          <h3>This contact changed since you opened it</h3>
          <p className={styles.muted}>Your edits are still in the form. The latest saved values:</p>
          <ul aria-label="Changes saved elsewhere">
            {(otherChanges.length ? otherChanges : FIELDS.slice(0, 1)).map((f) => (
              <li key={f}>
                {FIELD_LABELS[f]}: {theirs[f] || "(empty)"}
              </li>
            ))}
          </ul>
          <div className={styles.actions}>
            <button type="button" className={styles.primary} onClick={() => save(conflict.version)}>
              Save my changes again
            </button>
            <button type="button" className={styles.button} onClick={onDone}>
              Discard my changes
            </button>
          </div>
        </div>
      ) : null}
      {error ? (
        <p role="alert" className={`${styles.alert} ${styles.wide}`}>
          {error}
        </p>
      ) : null}
      <div className={`${styles.actions} ${styles.wide}`}>
        {conflict ? null : (
          <button type="submit" className={styles.primary} disabled={busy}>
            {busy ? "Saving…" : "Save contact"}
          </button>
        )}
        <button type="button" className={styles.button} onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}
