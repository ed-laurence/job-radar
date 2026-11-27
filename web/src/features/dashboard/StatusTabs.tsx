import type { Schemas } from "@/lib/api";
import styles from "./Dashboard.module.css";
import { isRoleTab, PANELS, TABS, type View } from "./tabs";

export interface StatusTabsProps {
  tabs: readonly View[];
  current: View;
  counts: Schemas["StatusTabCounts"] | undefined;
  /** Applied roles with a follow-up due, shown as "· N to follow up" on Applied. */
  followupsDue?: number;
  onChange: (tab: View) => void;
}

function countFor(view: View, counts: Schemas["StatusTabCounts"] | undefined): number | undefined {
  if (!counts) return undefined;
  if (isRoleTab(view)) return counts[TABS[view].count];
  if (view === "outreach") return counts.outreach;
  if (view === "feedback") return counts.feedback;
  return undefined;
}

/** A group (fieldset) of toggle buttons; not ARIA tabs, since the list below is one region. */
export function StatusTabs({ tabs, current, counts, followupsDue, onChange }: StatusTabsProps) {
  return (
    <fieldset className={styles.tabs}>
      <legend className="visually-hidden">Status</legend>
      {tabs.map((t) => {
        const n = countFor(t, counts);
        const hint = t === "applied" && followupsDue ? ` · ${followupsDue} to follow up` : "";
        return (
          <button
            key={t}
            type="button"
            className={styles.tab}
            aria-pressed={t === current}
            title={isRoleTab(t) ? undefined : PANELS[t].title}
            onClick={() => onChange(t)}
          >
            {isRoleTab(t) ? TABS[t].label : PANELS[t].label}
            {n === undefined ? null : (
              <span className={styles.tabCount}>
                {" "}
                {n}
                {hint}
              </span>
            )}
          </button>
        );
      })}
    </fieldset>
  );
}
