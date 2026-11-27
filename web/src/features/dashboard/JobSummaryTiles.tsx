import type { Schemas } from "@/lib/api";
import { ago } from "./card";
import styles from "./Dashboard.module.css";
import { hours } from "./series";

export interface JobSummaryTilesProps {
  summary: Schemas["JobSummary"];
}

const optional = (n: number | null | undefined) => (n === null ? undefined : n);

/** The four headline tiles from job-seeker's main page; To act on is highlighted. */
export function JobSummaryTiles({ summary }: JobSummaryTilesProps) {
  const oldest = optional(summary.oldest_waiting_hours);
  const tiles: { label: string; value: string; alert?: boolean }[] = [
    { label: "To act on", value: String(summary.to_act_on), alert: true },
    { label: "Oldest waiting", value: oldest === undefined ? "—" : ago(oldest * 3_600_000) },
    { label: "Applied, 7 days", value: String(summary.applied_last_7_days) },
    { label: "Median post→apply", value: hours(optional(summary.median_post_to_apply_hours)) },
  ];
  return (
    <section aria-label="Headline numbers">
      <dl className={styles.kpis}>
        {tiles.map((t) => (
          <div key={t.label} className={t.alert ? `${styles.kpi} ${styles.kpiAlert}` : styles.kpi}>
            <dt>{t.label}</dt>
            <dd>{t.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
