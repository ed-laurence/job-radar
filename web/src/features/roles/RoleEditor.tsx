"use client";

import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useId, useState } from "react";
import { describeError, isVersionConflict, type Schemas, unwrap, useApiClient } from "@/lib/api";
import { roleKeys, useRoleWritePending, useUpdateRole } from "./api";
import { type Role, TRACK_LABELS, TRACKS, WORK_MODE_LABELS, WORK_MODES } from "./labels";
import styles from "./Roles.module.css";

const TEXT_FIELDS = [
  { name: "title", label: "Title", max: 300 },
  { name: "company", label: "Company", max: 200 },
  { name: "location", label: "Location", max: 200 },
  { name: "pay", label: "Pay", max: 100 },
  { name: "url", label: "Link to the ad", max: 2048 },
  { name: "mission", label: "Mission", max: 100 },
] as const;

type TextField = (typeof TEXT_FIELDS)[number]["name"] | "notes";
type Field = TextField | "work_mode" | "track";
type Draft = Record<Field, string>;

const FIELD_LABELS: Record<Field, string> = {
  title: "Title",
  company: "Company",
  location: "Location",
  pay: "Pay",
  url: "Link",
  mission: "Mission",
  notes: "Notes",
  work_mode: "Work mode",
  track: "Track",
};
const FIELDS = Object.keys(FIELD_LABELS) as Field[];

function toDraft(role: Role): Draft {
  return {
    title: role.title,
    company: role.company ?? "",
    location: role.location ?? "",
    pay: role.pay ?? "",
    url: role.url ?? "",
    mission: role.mission ?? "",
    notes: role.notes ?? "",
    work_mode: role.work_mode ?? "",
    track: role.track ?? "",
  };
}

/** Only fields the user changed are sent, so a concurrent edit to another field isn't clobbered. */
function changes(base: Draft, draft: Draft): Partial<Draft> {
  const out: Partial<Draft> = {};
  for (const f of FIELDS) {
    if (draft[f].trim() !== base[f].trim()) out[f] = draft[f].trim();
  }
  return out;
}

function toUpdate(version: number, changed: Partial<Draft>): Schemas["RoleUpdate"] {
  const body: Schemas["RoleUpdate"] = { version };
  for (const [key, value] of Object.entries(changed) as [Field, string][]) {
    if (key === "work_mode") {
      if (value) body.work_mode = value as Schemas["WorkMode"];
    } else if (key === "track") {
      if (value) body.track = value as Schemas["Track"];
    } else if (key === "url") {
      if (value) body.url = value;
    } else {
      body[key] = value;
    }
  }
  return body;
}

export interface RoleEditorProps {
  role: Role;
}

/**
 * Edits with the version that was read. On a 409 the latest version is fetched and shown next to
 * the user's draft; the user decides whether to re-apply their changes on top of it.
 */
export function RoleEditor({ role }: RoleEditorProps) {
  const id = useId();
  const client = useApiClient();
  const queryClient = useQueryClient();
  const update = useUpdateRole(role.id);
  const writing = useRoleWritePending(role.id);
  const [editing, setEditing] = useState(false);
  const [base, setBase] = useState<Role>(role);
  const [draft, setDraft] = useState<Draft>(() => toDraft(role));
  const [conflict, setConflict] = useState<{ before: Role; latest: Role } | null>(null);
  const [notice, setNotice] = useState("");

  const begin = () => {
    setBase(role);
    setDraft(toDraft(role));
    setConflict(null);
    setNotice("");
    update.reset();
    setEditing(true);
  };

  const save = (against: Role) => {
    const changed = changes(toDraft(base), draft);
    if (Object.keys(changed).length === 0) {
      setEditing(false);
      return;
    }
    update.mutate(toUpdate(against.version, changed), {
      onSuccess: () => {
        setEditing(false);
        setConflict(null);
        setNotice("Changes saved.");
      },
      onError: async (error) => {
        if (!isVersionConflict(error)) return;
        const latest = await queryClient.fetchQuery({
          queryKey: roleKeys.detail(role.id),
          queryFn: () =>
            unwrap(client.GET("/roles/{roleId}", { params: { path: { roleId: role.id } } })),
          staleTime: 0,
        });
        setConflict({ before: against, latest });
      },
    });
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save(conflict?.latest ?? base);
  };

  const discard = () => {
    if (!conflict) return;
    setBase(conflict.latest);
    setDraft(toDraft(conflict.latest));
    setConflict(null);
    update.reset();
  };

  if (!editing) {
    return (
      <div className={styles.actions}>
        <button type="button" className={styles.button} onClick={begin}>
          Edit role
        </button>
        <p role="status" className={styles.notice}>
          {notice}
        </p>
      </div>
    );
  }

  const otherChanges = conflict
    ? FIELDS.filter((f) => toDraft(conflict.before)[f] !== toDraft(conflict.latest)[f])
    : [];
  const otherError =
    update.isError && !isVersionConflict(update.error) ? describeError(update.error) : null;

  return (
    <form className={`${styles.panel} ${styles.form}`} onSubmit={submit} aria-label="Edit role">
      {TEXT_FIELDS.map((f) => (
        <div key={f.name} className={styles.field}>
          <label htmlFor={`${id}-${f.name}`}>{f.label}</label>
          <input
            id={`${id}-${f.name}`}
            className={styles.input}
            maxLength={f.max}
            value={draft[f.name]}
            onChange={(e) => setDraft((d) => ({ ...d, [f.name]: e.target.value }))}
          />
        </div>
      ))}
      <div className={styles.field}>
        <label htmlFor={`${id}-mode`}>Work mode</label>
        <select
          id={`${id}-mode`}
          className={styles.select}
          value={draft.work_mode}
          onChange={(e) => setDraft((d) => ({ ...d, work_mode: e.target.value }))}
        >
          <option value="">Unknown</option>
          {WORK_MODES.map((m) => (
            <option key={m} value={m}>
              {WORK_MODE_LABELS[m]}
            </option>
          ))}
        </select>
      </div>
      <div className={styles.field}>
        <label htmlFor={`${id}-track`}>Track</label>
        <select
          id={`${id}-track`}
          className={styles.select}
          value={draft.track}
          onChange={(e) => setDraft((d) => ({ ...d, track: e.target.value }))}
        >
          <option value="">Unknown</option>
          {TRACKS.map((t) => (
            <option key={t} value={t}>
              {TRACK_LABELS[t]}
            </option>
          ))}
        </select>
      </div>
      <div className={`${styles.field} ${styles.wide}`}>
        <label htmlFor={`${id}-notes`}>Notes</label>
        <textarea
          id={`${id}-notes`}
          className={styles.textarea}
          maxLength={8000}
          value={draft.notes}
          onChange={(e) => setDraft((d) => ({ ...d, notes: e.target.value }))}
        />
      </div>

      {conflict ? (
        <div role="alert" className={`${styles.alert} ${styles.wide}`}>
          <h3>This role changed since you opened it</h3>
          <p className={styles.muted}>
            Version {conflict.latest.version} is now saved. Your edits are still in the form.
          </p>
          {otherChanges.length ? (
            <ul aria-label="Changes saved elsewhere">
              {otherChanges.map((f) => (
                <li key={f}>
                  {FIELD_LABELS[f]}: {toDraft(conflict.before)[f] || "(empty)"} →{" "}
                  {toDraft(conflict.latest)[f] || "(empty)"}
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.muted}>Only fields you are not editing changed.</p>
          )}
          <div className={styles.actions}>
            <button type="button" className={styles.primary} onClick={() => save(conflict.latest)}>
              Save my changes again
            </button>
            <button type="button" className={styles.button} onClick={discard}>
              Discard my changes
            </button>
          </div>
        </div>
      ) : null}
      {otherError ? (
        <div role="alert" className={`${styles.alert} ${styles.wide}`}>
          <h3>{otherError.title}</h3>
          {[otherError.description, ...otherError.fieldErrors].filter(Boolean).map((m) => (
            <p key={m} className={styles.muted}>
              {m}
            </p>
          ))}
          {otherError.correlationId ? <p>Reference: {otherError.correlationId}</p> : null}
        </div>
      ) : null}

      <div className={`${styles.actions} ${styles.wide}`}>
        {conflict ? null : (
          <button type="submit" className={styles.primary} disabled={writing}>
            {update.isPending ? "Saving…" : "Save changes"}
          </button>
        )}
        <button type="button" className={styles.button} onClick={() => setEditing(false)}>
          Cancel
        </button>
      </div>
    </form>
  );
}
