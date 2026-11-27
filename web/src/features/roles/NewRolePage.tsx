"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useId, useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { useAnalytics } from "@/lib/AnalyticsProvider";
import type { Schemas } from "@/lib/api";
import { describeError } from "@/lib/api";
import { useCreateRole } from "./api";
import { WORK_MODE_LABELS, WORK_MODES, type WorkMode } from "./labels";
import styles from "./Roles.module.css";

const TEXT_FIELDS = [
  { name: "company", label: "Company", max: 200 },
  { name: "url", label: "Link to the ad", max: 2048, type: "url" },
  { name: "location", label: "Location", max: 200 },
  { name: "pay", label: "Pay", max: 100 },
] as const;

type Draft = Record<
  "title" | "company" | "url" | "location" | "pay" | "description" | "notes",
  string
>;

const EMPTY: Draft = {
  title: "",
  company: "",
  url: "",
  location: "",
  pay: "",
  description: "",
  notes: "",
};

/** Manual import. The ad text is untrusted: the API guards it before any model sees it. */
export function NewRolePage() {
  const id = useId();
  const router = useRouter();
  const { track } = useAnalytics();
  const create = useCreateRole();
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [workMode, setWorkMode] = useState<WorkMode | "">("");
  const [scoreNow, setScoreNow] = useState(false);

  const set = (name: keyof Draft) => (value: string) => setDraft((d) => ({ ...d, [name]: value }));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const body: Schemas["RoleCreate"] = {
      source: "manual",
      title: draft.title.trim(),
      score_now: scoreNow,
    };
    for (const key of ["company", "url", "location", "pay", "description", "notes"] as const) {
      const value = draft[key].trim();
      if (value) body[key] = value;
    }
    if (workMode) body.work_mode = workMode;
    create.mutate(body, {
      onSuccess: (role) => {
        track("role_created");
        router.push(`/roles/${role.id}`);
      },
    });
  };

  const error = create.isError ? describeError(create.error, "Import failed") : null;

  return (
    <section className={styles.page} aria-labelledby="page-title">
      <div className={styles.header}>
        <h1 id="page-title">Import a role</h1>
        <Link href="/roles">Back to roles</Link>
      </div>
      <form className={`${styles.panel} ${styles.form}`} onSubmit={submit}>
        <div className={styles.field}>
          <label htmlFor={`${id}-title`}>
            Title <span>(required)</span>
          </label>
          <input
            id={`${id}-title`}
            className={styles.input}
            required
            maxLength={300}
            value={draft.title}
            onChange={(e) => set("title")(e.target.value)}
          />
        </div>
        {TEXT_FIELDS.map((f) => (
          <div key={f.name} className={styles.field}>
            <label htmlFor={`${id}-${f.name}`}>{f.label}</label>
            <input
              id={`${id}-${f.name}`}
              className={styles.input}
              type={"type" in f ? f.type : "text"}
              maxLength={f.max}
              value={draft[f.name]}
              onChange={(e) => set(f.name)(e.target.value)}
            />
          </div>
        ))}
        <div className={styles.field}>
          <label htmlFor={`${id}-mode`}>Work mode</label>
          <select
            id={`${id}-mode`}
            className={styles.select}
            value={workMode}
            onChange={(e) => setWorkMode(e.target.value as WorkMode | "")}
          >
            <option value="">Unknown</option>
            {WORK_MODES.map((m) => (
              <option key={m} value={m}>
                {WORK_MODE_LABELS[m]}
              </option>
            ))}
          </select>
        </div>
        <div className={`${styles.field} ${styles.wide}`}>
          <label htmlFor={`${id}-ad`}>Ad text</label>
          <textarea
            id={`${id}-ad`}
            className={styles.textarea}
            maxLength={50000}
            value={draft.description}
            onChange={(e) => set("description")(e.target.value)}
          />
        </div>
        <div className={`${styles.field} ${styles.wide}`}>
          <label htmlFor={`${id}-notes`}>Notes</label>
          <textarea
            id={`${id}-notes`}
            className={styles.textarea}
            maxLength={8000}
            value={draft.notes}
            onChange={(e) => set("notes")(e.target.value)}
          />
        </div>
        <label className={`${styles.check} ${styles.wide}`}>
          <input
            type="checkbox"
            checked={scoreNow}
            onChange={(e) => setScoreNow(e.target.checked)}
          />
          Score it after import
        </label>
        {error ? (
          <div className={styles.wide}>
            <ErrorState
              title={error.title}
              description={[error.description, ...error.fieldErrors].filter(Boolean).join(" ")}
              correlationId={error.correlationId}
            />
          </div>
        ) : null}
        <div className={`${styles.actions} ${styles.wide}`}>
          <button type="submit" className={styles.primary} disabled={create.isPending}>
            {create.isPending ? "Importing…" : "Import role"}
          </button>
        </div>
      </form>
    </section>
  );
}
