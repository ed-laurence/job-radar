"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useHealthSummary } from "./api";
import styles from "./Health.module.css";
import { HealthPanel } from "./HealthPanel";
import { jobsHealth, jobsHealthText } from "./health";

export interface HealthMenuProps {
  maintainer: boolean;
}

/**
 * The status dot and the Health toggle for the top bar. The panel opens under the bar (a
 * disclosure, not a dialog): Escape closes it and returns focus to the toggle.
 */
export function HealthMenu({ maintainer }: HealthMenuProps) {
  const id = useId();
  const panelId = `${id}-panel`;
  const [open, setOpen] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);
  const summary = useHealthSummary(maintainer);
  const jobs = summary.data?.jobs;
  const state = maintainer ? jobsHealth(jobs) : "unknown";
  const text = maintainer ? jobsHealthText(jobs) : "Scheduled jobs: maintainers only";

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      toggle.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className={styles.menu}>
      <span className={styles.status} title={text}>
        <span className={`${styles.dot} ${styles[state]}`} aria-hidden="true" />
        <span className="visually-hidden">{text}</span>
      </span>
      <button
        ref={toggle}
        type="button"
        className={styles.toggle}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
      >
        Health
      </button>
      <section id={panelId} className={styles.panel} aria-label="Health" hidden={!open}>
        {open ? <HealthPanel maintainer={maintainer} /> : null}
      </section>
    </div>
  );
}
