"use client";

import { useId, useState } from "react";
import { AGE_MAX_DAYS, AGE_PRESETS, ageError, CUSTOM_AGE, presetOf, rangeOf } from "./age";
import type { RoleFilters } from "./api";
import {
  type RoleSort,
  SORTS,
  STATUS_LABELS,
  STATUSES,
  TRACK_LABELS,
  TRACKS,
  type Track,
} from "./labels";
import styles from "./Roles.module.css";

export interface RoleFiltersBarProps {
  value: RoleFilters;
  onChange: (next: RoleFilters) => void;
}

export function RoleFiltersBar({ value, onChange }: RoleFiltersBarProps) {
  const id = useId();
  const set = (patch: Partial<RoleFilters>) => onChange({ ...value, ...patch });
  const [customAge, setCustomAge] = useState(() => presetOf(value.age) === CUSTOM_AGE);
  const agePreset = customAge ? CUSTOM_AGE : presetOf(value.age);
  const ageProblem = ageError(value.age);
  const ageDay = (raw: number) =>
    Number.isNaN(raw) ? ("" as const) : Math.max(0, Math.min(AGE_MAX_DAYS, Math.round(raw)));
  const toggleStatus = (status: RoleFilters["status"][number]) =>
    set({
      status: value.status.includes(status)
        ? value.status.filter((s) => s !== status)
        : [...value.status, status],
    });

  return (
    <search className={styles.filters} aria-label="Filter roles">
      <div className={styles.field}>
        <label htmlFor={`${id}-q`}>Search title or company</label>
        <input
          id={`${id}-q`}
          type="search"
          className={styles.input}
          value={value.q}
          maxLength={100}
          onChange={(e) => set({ q: e.target.value })}
        />
      </div>
      <fieldset className={styles.fieldset}>
        <legend className={styles.legend}>Status</legend>
        {STATUSES.map((status) => (
          <label key={status} className={styles.check}>
            <input
              type="checkbox"
              checked={value.status.includes(status)}
              onChange={() => toggleStatus(status)}
            />
            {STATUS_LABELS[status]}
          </label>
        ))}
      </fieldset>
      <div className={styles.field}>
        <label htmlFor={`${id}-track`}>Track</label>
        <select
          id={`${id}-track`}
          className={styles.select}
          value={value.track}
          onChange={(e) => set({ track: e.target.value as Track | "" })}
        >
          <option value="">Any</option>
          {TRACKS.map((t) => (
            <option key={t} value={t}>
              {TRACK_LABELS[t]}
            </option>
          ))}
        </select>
      </div>
      <div className={styles.field}>
        <label htmlFor={`${id}-score`}>Minimum score</label>
        <input
          id={`${id}-score`}
          type="number"
          min={0}
          max={100}
          className={styles.input}
          value={value.minScore}
          onChange={(e) => {
            const n = e.target.valueAsNumber;
            set({ minScore: Number.isNaN(n) ? "" : Math.max(0, Math.min(100, Math.round(n))) });
          }}
        />
      </div>
      <div className={styles.field}>
        <label htmlFor={`${id}-age`}>Age</label>
        <select
          id={`${id}-age`}
          className={styles.select}
          value={agePreset}
          onChange={(e) => {
            const range = rangeOf(e.target.value);
            setCustomAge(!range);
            if (range) set({ age: range });
          }}
        >
          {AGE_PRESETS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
          <option value={CUSTOM_AGE}>Custom range…</option>
        </select>
      </div>
      {agePreset === CUSTOM_AGE ? (
        <fieldset
          className={styles.fieldset}
          aria-describedby={ageProblem ? `${id}-age-error` : undefined}
        >
          <legend className={styles.legend}>Age in days</legend>
          <label className={styles.check} htmlFor={`${id}-age-min`}>
            From
            <input
              id={`${id}-age-min`}
              type="number"
              min={0}
              max={AGE_MAX_DAYS}
              className={styles.input}
              value={value.age.min}
              aria-invalid={ageProblem ? true : undefined}
              onChange={(e) => set({ age: { ...value.age, min: ageDay(e.target.valueAsNumber) } })}
            />
          </label>
          <label className={styles.check} htmlFor={`${id}-age-max`}>
            to
            <input
              id={`${id}-age-max`}
              type="number"
              min={0}
              max={AGE_MAX_DAYS}
              className={styles.input}
              value={value.age.max}
              aria-invalid={ageProblem ? true : undefined}
              onChange={(e) => set({ age: { ...value.age, max: ageDay(e.target.valueAsNumber) } })}
            />
          </label>
          {ageProblem ? (
            <p id={`${id}-age-error`} role="alert" className={styles.error}>
              {ageProblem}
            </p>
          ) : null}
        </fieldset>
      ) : null}
      <div className={styles.field}>
        <label htmlFor={`${id}-sort`}>Sort</label>
        <select
          id={`${id}-sort`}
          className={styles.select}
          value={value.sort}
          onChange={(e) => set({ sort: e.target.value as RoleSort })}
        >
          {(Object.keys(SORTS) as RoleSort[]).map((s) => (
            <option key={s} value={s}>
              {SORTS[s]}
            </option>
          ))}
        </select>
      </div>
    </search>
  );
}
