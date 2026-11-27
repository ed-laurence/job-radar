"use client";

import { type FormEvent, useId, useState } from "react";
import { describeError, isVersionConflict, type Schemas } from "@/lib/api";
import { type Target, useCreateTarget, useFetchTarget, useUpdateTarget } from "./api";
import {
  LANE_LABELS,
  LANES,
  STATUS_LABELS,
  STATUSES,
  type TargetLane,
  type TargetStatus,
} from "./labels";
import styles from "./Targets.module.css";

const TEXT_FIELDS = [
  { name: "why", label: "Why", max: 2000 },
  { name: "approach", label: "Approach", max: 1000 },
  { name: "caveat", label: "Caveat", max: 1000 },
] as const;

type TextField = (typeof TEXT_FIELDS)[number]["name"] | "notes";
type Field = "name" | "lane" | "status" | TextField | "sources";
type Draft = Record<Field, string>;

const FIELD_LABELS: Record<Field, string> = {
  name: "Name",
  lane: "Lane",
  status: "Status",
  why: "Why",
  approach: "Approach",
  caveat: "Caveat",
  sources: "Sources",
  notes: "Notes",
};
const FIELDS = Object.keys(FIELD_LABELS) as Field[];
const MAX_SOURCES = 20;

function toDraft(t: Target | undefined): Draft {
  return {
    name: t?.name ?? "",
    lane: t?.lane ?? "A",
    status: t?.status ?? "suggested",
    why: t?.why ?? "",
    approach: t?.approach ?? "",
    caveat: t?.caveat ?? "",
    sources: (t?.sources ?? []).join("\n"),
    notes: t?.notes ?? "",
  };
}

/** One link per line; blank lines and surrounding spaces don't count. */
function toSources(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function sameValue(field: Field, a: string, b: string): boolean {
  return field === "sources"
    ? toSources(a).join("\n") === toSources(b).join("\n")
    : a.trim() === b.trim();
}

/**
 * The API never takes nulls. A new target gets only filled-in fields; an edit sends only what the
 * user changed (so a concurrent edit to another field isn't clobbered) and may blank a text field.
 */
function fill(body: Partial<Schemas["TargetCreate"]>, draft: Partial<Draft>, clearing: boolean) {
  for (const [key, raw] of Object.entries(draft) as [Field, string][]) {
    const value = raw.trim();
    if (key === "lane") body.lane = value as TargetLane;
    else if (key === "status") body.status = value as TargetStatus;
    else if (key === "name") body.name = value;
    else if (key === "sources") {
      const sources = toSources(raw);
      if (sources.length || clearing) body.sources = sources;
    } else if (value || clearing) body[key] = value;
  }
}

export interface TargetFormProps {
  /** Absent for a new target. */
  target?: Target;
  onDone: () => void;
}

/**
 * Creates or edits a target. An edit is sent with the version that was read; on a 409 the latest
 * version is shown next to the user's draft and the user decides whether to re-apply it.
 */
export function TargetForm({ target, onDone }: TargetFormProps) {
  const id = useId();
  const create = useCreateTarget();
  const update = useUpdateTarget();
  const fetchTarget = useFetchTarget();
  const [draft, setDraft] = useState<Draft>(() => toDraft(target));
  const [conflict, setConflict] = useState<Target | null>(null);
  const [error, setError] = useState<string[] | null>(null);
  const base = toDraft(target);
  const busy = create.isPending || update.isPending;
  const sourceCount = toSources(draft.sources).length;

  const set = (field: Field) => (value: string) => setDraft((d) => ({ ...d, [field]: value }));

  const showError = (e: unknown) => {
    const view = describeError(e, "The employer didn't save");
    setError([view.title, view.description, ...view.fieldErrors].filter((m): m is string => !!m));
  };

  const save = (version: number) => {
    setError(null);
    if (!target) {
      const body: Schemas["TargetCreate"] = {
        name: draft.name.trim(),
        lane: draft.lane as TargetLane,
      };
      fill(body, draft, false);
      create.mutate(body, { onSuccess: onDone, onError: showError });
      return;
    }
    const changed: Partial<Draft> = {};
    for (const f of FIELDS) if (!sameValue(f, draft[f], base[f])) changed[f] = draft[f];
    if (Object.keys(changed).length === 0) {
      onDone();
      return;
    }
    const body: Schemas["TargetUpdate"] = { version };
    fill(body, changed, true);
    update.mutate(
      { id: target.id, body },
      {
        onSuccess: onDone,
        onError: async (e) => {
          if (!isVersionConflict(e)) {
            showError(e);
            return;
          }
          try {
            setConflict(await fetchTarget(target.id));
          } catch (fetchError) {
            showError(fetchError);
          }
        },
      },
    );
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save(conflict?.version ?? target?.version ?? 1);
  };

  const theirs = conflict ? toDraft(conflict) : null;
  const otherChanges = theirs ? FIELDS.filter((f) => !sameValue(f, theirs[f], base[f])) : [];
  const formName = target ? `Edit ${target.name}` : "New employer";

  return (
    <form className={`${styles.panel} ${styles.form}`} aria-label={formName} onSubmit={submit}>
      <h2 className={styles.wide}>{formName}</h2>
      <div className={styles.field}>
        <label htmlFor={`${id}-name`}>
          Name <span>(required)</span>
        </label>
        <input
          id={`${id}-name`}
          className={styles.input}
          required
          maxLength={200}
          value={draft.name}
          onChange={(e) => set("name")(e.target.value)}
        />
      </div>
      <div className={styles.field}>
        <label htmlFor={`${id}-lane`}>Lane</label>
        <select
          id={`${id}-lane`}
          className={styles.select}
          value={draft.lane}
          onChange={(e) => set("lane")(e.target.value)}
        >
          {LANES.map((l) => (
            <option key={l} value={l}>
              {LANE_LABELS[l]}
            </option>
          ))}
        </select>
      </div>
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
      {TEXT_FIELDS.map((f) => (
        <div key={f.name} className={`${styles.field} ${styles.wide}`}>
          <label htmlFor={`${id}-${f.name}`}>{f.label}</label>
          <textarea
            id={`${id}-${f.name}`}
            className={styles.textarea}
            maxLength={f.max}
            value={draft[f.name]}
            onChange={(e) => set(f.name)(e.target.value)}
          />
        </div>
      ))}
      <div className={`${styles.field} ${styles.wide}`}>
        <label htmlFor={`${id}-sources`}>Sources (one link per line)</label>
        <textarea
          id={`${id}-sources`}
          className={styles.textarea}
          aria-describedby={`${id}-sources-hint`}
          value={draft.sources}
          onChange={(e) => set("sources")(e.target.value)}
        />
        <p id={`${id}-sources-hint`} className={styles.muted}>
          {sourceCount} of {MAX_SOURCES} links
        </p>
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
          <h3>This employer changed since you opened it</h3>
          <p className={styles.muted}>
            Version {conflict.version} is now saved. Your edits are still in the form.
          </p>
          {otherChanges.length ? (
            <ul aria-label="Changes saved elsewhere">
              {otherChanges.map((f) => (
                <li key={f}>
                  {FIELD_LABELS[f]}: {theirs[f] || "(empty)"}
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.muted}>Only fields you are not editing changed.</p>
          )}
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
        <div role="alert" className={`${styles.alert} ${styles.wide}`}>
          {error.map((m) => (
            <p key={m} className={styles.alertText}>
              {m}
            </p>
          ))}
        </div>
      ) : null}
      <div className={`${styles.actions} ${styles.wide}`}>
        {conflict ? null : (
          <button
            type="submit"
            className={styles.primary}
            disabled={busy || sourceCount > MAX_SOURCES}
          >
            {busy ? "Saving…" : "Save employer"}
          </button>
        )}
        <button type="button" className={styles.button} onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}
