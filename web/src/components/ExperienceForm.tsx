"use client";

import { type FormEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";
import { flushSync } from "react-dom";
import type { Schemas } from "@/lib/api";
import rows from "./ExperienceEntries.module.css";
import styles from "./ExperienceForm.module.css";

/*
 * The experience-record editor, shared by Settings ("Experience not on your CV") and the evidence
 * block's "I've done this": the requirement it answers, then one row per role or project where
 * you did it (job-seeker's entry rows). Presentational: fields in, a validated, trimmed record out.
 */

export type ExperienceEntry = Schemas["ExperienceEntry"];

export interface ExperienceFields {
  requirement: string;
  entries: ExperienceEntry[];
}

export const EXPERIENCE_LIMITS = {
  requirement: 200,
  context: 200,
  detail: 1500,
  entries: 10,
} as const;

export const EMPTY_EXPERIENCE: ExperienceFields = { requirement: "", entries: [] };

const ROW_PLACEHOLDER = "Choose the role or project…";
const DETAIL_PLACEHOLDER = "Specific and true; numbers only if they're real";

/** The rows that were filled in (a row with both fields blank is dropped), trimmed. */
export function readEntries(entries: readonly ExperienceEntry[]): ExperienceEntry[] {
  return entries
    .map((e) => ({ context: e.context.trim(), detail: e.detail.trim() }))
    .filter((e) => e.context || e.detail);
}

export interface EntriesProblem {
  message: string;
  /** The row to fix (its position among all rows, blank ones included). */
  index: number;
  field: "context" | "detail";
}

/** job-seeker's check before saving: every filled row needs both where and what was done. */
export function entriesProblem(entries: readonly ExperienceEntry[]): EntriesProblem | null {
  const filled = entries
    .map((e, index) => ({ context: e.context.trim(), detail: e.detail.trim(), index }))
    .filter((e) => e.context || e.detail);
  if (!filled.length) {
    return { message: "Add at least one role and what you did there", index: 0, field: "context" };
  }
  const noContext = filled.find((e) => !e.context);
  if (noContext) {
    return {
      message: "Choose where you did it for each row",
      index: noContext.index,
      field: "context",
    };
  }
  const noDetail = filled.find((e) => !e.detail);
  if (noDetail) {
    return { message: "Say what you did in each role", index: noDetail.index, field: "detail" };
  }
  return null;
}

interface Row extends ExperienceEntry {
  key: number;
}

interface Problem {
  message: string;
  /** The control that's wrong: it gets focus, aria-invalid and the message as its description. */
  target: string;
}

export interface ExperienceFormProps {
  /** The form's accessible name (for a record, its requirement). */
  name: string;
  /** A visible heading above the fields (e.g. "Add one"). */
  heading?: ReactNode;
  initial: ExperienceFields;
  /** The "Where" suggestions; a saved value that isn't one of them stays selectable. */
  contexts: readonly string[];
  /** False when the requirement is fixed (it's the gap "I've done this" was clicked on). */
  showRequirement?: boolean;
  requirementPlaceholder?: string;
  /** Shown beside the requirement label (the record's ref and how often it's matched). */
  meta?: ReactNode;
  submitLabel: string;
  busy: boolean;
  /** A server failure or conflict, already worded; announced when it appears. */
  error: ReactNode;
  onSubmit: (fields: ExperienceFields) => void;
  onCancel?: () => void;
  /** More buttons after the submit button (Delete). */
  actions?: ReactNode;
  /** Below the buttons (a delete confirmation). */
  children?: ReactNode;
  className?: string;
  /** Move focus to the first field on mount (the add form, after an add). */
  focusOnMount?: boolean;
}

export function ExperienceForm({
  name,
  heading,
  initial,
  contexts,
  showRequirement = true,
  requirementPlaceholder,
  meta,
  submitLabel,
  busy,
  error,
  onSubmit,
  onCancel,
  actions,
  children,
  className,
  focusOnMount = false,
}: ExperienceFormProps) {
  const id = useId();
  const nextKey = useRef(0);
  const makeRow = (e: ExperienceEntry): Row => {
    nextKey.current += 1;
    return { key: nextKey.current, context: e.context, detail: e.detail };
  };
  const [requirement, setRequirement] = useState(initial.requirement);
  const [entries, setEntries] = useState<Row[]>(() =>
    (initial.entries.length ? initial.entries : [{ context: "", detail: "" }]).map(makeRow),
  );
  const [problem, setProblem] = useState<Problem | null>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  const formRef = useRef<HTMLFormElement>(null);

  const reqId = `${id}-requirement`;
  const metaId = `${id}-meta`;
  const problemId = `${id}-problem`;
  const ctxId = (r: Row) => `${id}-r${r.key}-context`;
  const detId = (r: Row) => `${id}-r${r.key}-detail`;

  useEffect(() => {
    if (focusOnMount) formRef.current?.querySelector<HTMLElement>("input, select")?.focus();
  }, [focusOnMount]);

  const invalid = (target: string) =>
    problem?.target === target
      ? ({ "aria-invalid": true, "aria-describedby": problemId } as const)
      : {};

  const change = (key: number, patch: Partial<ExperienceEntry>) => {
    setProblem(null);
    setEntries((list) => list.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  };

  const addRow = () => {
    const row = makeRow({ context: "", detail: "" });
    flushSync(() => setEntries((list) => [...list, row]));
    document.getElementById(ctxId(row))?.focus();
  };

  const removeRow = (key: number) => {
    setProblem(null);
    const rest = entries.filter((r) => r.key !== key);
    if (rest.length) {
      flushSync(() => setEntries(rest));
      addRef.current?.focus();
      return;
    }
    // The last row clears rather than disappears: there's always a row to fill in.
    const blank = makeRow({ context: "", detail: "" });
    flushSync(() => setEntries([blank]));
    document.getElementById(ctxId(blank))?.focus();
  };

  const fail = (message: string, target: string) => {
    flushSync(() => setProblem({ message, target }));
    document.getElementById(target)?.focus();
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const req = requirement.trim();
    if (!req) {
      fail("Say which requirement it answers", reqId);
      return;
    }
    const found = entriesProblem(entries);
    const row = found ? entries[found.index] : undefined;
    if (found && row) {
      fail(found.message, found.field === "context" ? ctxId(row) : detId(row));
      return;
    }
    setProblem(null);
    onSubmit({ requirement: req, entries: readEntries(entries) });
  };

  return (
    <form
      ref={formRef}
      className={`${rows.form} ${className ?? ""}`}
      aria-label={name}
      noValidate
      onSubmit={submit}
    >
      {heading ? <p className={rows.label}>{heading}</p> : null}
      {showRequirement ? (
        <div className={rows.ents}>
          <div className={rows.labelRow}>
            <label htmlFor={reqId} className={rows.label}>
              Requirement it answers
            </label>
            {meta ? (
              <span id={metaId} className={rows.labelRow}>
                {meta}
              </span>
            ) : null}
          </div>
          <input
            id={reqId}
            type="text"
            className={rows.control}
            value={requirement}
            maxLength={EXPERIENCE_LIMITS.requirement}
            placeholder={requirementPlaceholder}
            aria-describedby={
              [meta ? metaId : "", problem?.target === reqId ? problemId : ""]
                .filter(Boolean)
                .join(" ") || undefined
            }
            aria-invalid={problem?.target === reqId ? true : undefined}
            onChange={(ev) => {
              setProblem(null);
              setRequirement(ev.target.value);
            }}
          />
        </div>
      ) : null}
      <div className={rows.ents}>
        {entries.map((r, i) => {
          const known = !r.context || contexts.includes(r.context);
          return (
            <fieldset key={r.key} className={rows.ent} aria-label={`Entry ${i + 1}`}>
              <label>
                Where
                <select
                  id={ctxId(r)}
                  className={rows.control}
                  value={r.context}
                  {...invalid(ctxId(r))}
                  onChange={(ev) => change(r.key, { context: ev.target.value })}
                >
                  <option value="">{ROW_PLACEHOLDER}</option>
                  {contexts.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                  {known ? null : <option value={r.context}>{r.context}</option>}
                </select>
              </label>
              <label>
                What you did there
                <textarea
                  id={detId(r)}
                  className={rows.control}
                  rows={2}
                  value={r.detail}
                  maxLength={EXPERIENCE_LIMITS.detail}
                  placeholder={DETAIL_PLACEHOLDER}
                  {...invalid(detId(r))}
                  onChange={(ev) => change(r.key, { detail: ev.target.value })}
                />
              </label>
              <button
                type="button"
                className={`${rows.linkbtn} ${rows.danger}`}
                title="Remove this role"
                aria-label={`Remove entry ${i + 1}`}
                onClick={() => removeRow(r.key)}
              >
                Remove
              </button>
            </fieldset>
          );
        })}
      </div>
      <button
        ref={addRef}
        type="button"
        className={rows.linkbtn}
        disabled={entries.length >= EXPERIENCE_LIMITS.entries}
        onClick={addRow}
      >
        + Another role
      </button>
      {problem ? (
        <p id={problemId} className={rows.problem}>
          {problem.message}
        </p>
      ) : null}
      {error ? (
        <div role="alert" className={styles.inlineAlert}>
          {error}
        </div>
      ) : null}
      <div className={rows.actions}>
        <button type="submit" className={styles.primary} disabled={busy}>
          {busy ? "Saving…" : submitLabel}
        </button>
        {actions}
        {onCancel ? (
          <button type="button" className={styles.button} onClick={onCancel}>
            Cancel
          </button>
        ) : null}
      </div>
      {children}
    </form>
  );
}
