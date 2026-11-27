"use client";

import { type KeyboardEvent, useState } from "react";
import styles from "./Settings.module.css";

export interface ChipInputProps {
  id: string;
  label: string;
  values: readonly string[];
  max: number;
  maxLength: number;
  describedBy?: string;
  invalid?: boolean;
  onChange: (next: string[]) => void;
}

/**
 * A list of short values. Enter or comma adds what's typed; Backspace in an empty box removes
 * the last chip; each chip has its own labelled remove button. Changes are announced politely.
 */
export function ChipInput({
  id,
  label,
  values,
  max,
  maxLength,
  describedBy,
  invalid,
  onChange,
}: ChipInputProps) {
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const full = values.length >= max;

  const add = () => {
    const value = text.trim().replace(/\s+/g, " ");
    if (!value) return;
    if (values.some((v) => v.toLowerCase() === value.toLowerCase())) {
      setNote(`${value} is already in the list.`);
      return;
    }
    if (full) {
      setNote(`You can add up to ${max}.`);
      return;
    }
    onChange([...values, value]);
    setText("");
    setNote(`Added ${value}.`);
  };

  const remove = (value: string) => {
    onChange(values.filter((v) => v !== value));
    setNote(`Removed ${value}.`);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      add();
    } else if (e.key === "Backspace" && !text && values.length) {
      const last = values[values.length - 1];
      if (last !== undefined) remove(last);
    }
  };

  return (
    <div className={styles.chips}>
      {values.length ? (
        <ul className={styles.chipList} aria-label={`${label} added`}>
          {values.map((v) => (
            <li key={v} className={styles.chip}>
              <span>{v}</span>
              <button
                type="button"
                className={styles.chipRemove}
                aria-label={`Remove ${v}`}
                onClick={() => remove(v)}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <div className={styles.chipEntry}>
        <input
          id={id}
          className={styles.input}
          value={text}
          maxLength={maxLength}
          placeholder={full ? `${max} added` : "Type a place, then Enter"}
          aria-invalid={invalid ? true : undefined}
          aria-describedby={describedBy}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button
          type="button"
          className={styles.button}
          disabled={full || !text.trim()}
          onClick={add}
        >
          Add <span className={styles.visuallyHidden}>to {label}</span>
        </button>
      </div>
      <p className={styles.visuallyHidden} aria-live="polite">
        {note}
      </p>
    </div>
  );
}
