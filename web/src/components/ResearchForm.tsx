"use client";

import { type FormEvent, useId } from "react";
import type { ResearchFocus } from "@/lib/research";
import styles from "./Research.module.css";

export const RESEARCH_FOCUS_LABELS: Readonly<Record<ResearchFocus, string>> = {
  overview: "Overview",
  hiring_signals: "Hiring signals",
  people_to_approach: "People to approach",
  news: "News",
};
const FOCI = Object.keys(RESEARCH_FOCUS_LABELS) as ResearchFocus[];

export interface ResearchTargetOption {
  id: string;
  name: string;
}

export interface ResearchFormProps {
  /** When given, the form starts with a target picker; otherwise the caller fixes the target. */
  targets?: readonly ResearchTargetOption[];
  targetId?: string;
  onTargetChange?: (targetId: string) => void;
  focus: readonly ResearchFocus[];
  onFocusChange: (focus: ResearchFocus[]) => void;
  running: boolean;
  /** Called with the focus in spec order. */
  onStart: (focus: ResearchFocus[]) => void;
}

/** Picks what the research agent looks into, then starts it. */
export function ResearchForm({
  targets,
  targetId = "",
  onTargetChange,
  focus,
  onFocusChange,
  running,
  onStart,
}: ResearchFormProps) {
  const id = useId();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onStart(FOCI.filter((f) => focus.includes(f)));
  };
  const toggle = (f: ResearchFocus) =>
    onFocusChange(focus.includes(f) ? focus.filter((x) => x !== f) : [...focus, f]);

  return (
    <form className={styles.form} onSubmit={submit}>
      {targets ? (
        <div className={styles.field}>
          <label htmlFor={`${id}-target`}>Employer</label>
          <select
            id={`${id}-target`}
            className={styles.select}
            value={targetId}
            onChange={(e) => onTargetChange?.(e.target.value)}
          >
            {targets.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      <fieldset className={styles.fieldset} disabled={running}>
        <legend className={styles.legend}>Focus</legend>
        {FOCI.map((f) => (
          <label key={f} className={styles.check}>
            <input type="checkbox" checked={focus.includes(f)} onChange={() => toggle(f)} />
            {RESEARCH_FOCUS_LABELS[f]}
          </label>
        ))}
      </fieldset>
      <div className={styles.actions}>
        <button type="submit" className={styles.primary} disabled={running}>
          Start research
        </button>
      </div>
    </form>
  );
}
