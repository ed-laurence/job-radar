import type { Schemas } from "@/lib/api";
import { COLUMN_LABELS } from "./columns";

export type DevMetrics = Schemas["DevMetrics"];
type Outcome = DevMetrics["run_outcomes"][number]["outcome"];

export const OUTCOME_LABELS: Readonly<Record<Outcome, string>> = {
  succeeded: "Succeeded",
  failed: "Failed",
  killed: "Killed",
  running: "Running",
  queued: "Queued",
};

const opt = (n: number | null | undefined) => (n === null ? undefined : n);

export function hours(n: number | null | undefined): string {
  const v = opt(n);
  if (v === undefined) return "—";
  return v >= 48 ? `${(v / 24).toFixed(1)} days` : `${v.toFixed(1)} h`;
}

export function pct(n: number | null | undefined): string {
  const v = opt(n);
  return v === undefined ? "—" : `${Math.round(v * 100)}%`;
}

export function usd(n: number | null | undefined): string {
  const v = opt(n);
  return v === undefined ? "—" : `$${v.toFixed(2)}`;
}

export function tiles(m: DevMetrics): [string, string][] {
  return [
    ["Lead time p50", hours(m.lead_time_hours_p50)],
    ["Lead time p90", hours(m.lead_time_hours_p90)],
    ["Agent pass rate", pct(m.agent_pass_rate)],
    ["Avg review rounds", opt(m.avg_review_rounds)?.toFixed(1) ?? "—"],
    ["Out-of-scope rate", pct(m.out_of_scope_rate)],
    ["Avg cost per task", usd(m.avg_cost_per_task_usd)],
    ["Blocked", String(m.blocked_count)],
    ["Oldest in human review", hours(m.oldest_human_review_hours)],
  ];
}

const sum = (ns: number[]) => ns.reduce((a, b) => a + b, 0);

export function throughputRows(m: DevMetrics) {
  return [...m.throughput]
    .sort((a, b) => a.week_start.localeCompare(b.week_start))
    .map((t) => ({ label: t.week_start, count: t.done }));
}

export function throughputSummary(m: DevMetrics): string {
  const rows = throughputRows(m);
  if (!rows.length) return "No cards reached Done yet.";
  const last = rows.at(-1);
  return `${sum(rows.map((r) => r.count))} cards done over ${rows.length} weeks; ${last?.count} in the week of ${last?.label}.`;
}

export function wipRows(m: DevMetrics) {
  return m.wip_by_column
    .filter((w) => w.column !== "done")
    .map((w) => ({ label: COLUMN_LABELS[w.column], count: w.count }));
}

export function wipSummary(m: DevMetrics): string {
  const rows = wipRows(m);
  const total = sum(rows.map((r) => r.count));
  if (!total) return "No cards in progress.";
  const biggest = rows.reduce((a, b) => (b.count > a.count ? b : a));
  return `${total} cards in progress; the most are in ${biggest.label} (${biggest.count}).`;
}

export function columnTimeRows(m: DevMetrics) {
  return m.cycle_time_by_column.map((c) => ({
    label: COLUMN_LABELS[c.column],
    hours: c.hours_p50,
  }));
}

export function columnTimeSummary(m: DevMetrics): string {
  const rows = columnTimeRows(m);
  if (!rows.length) return "No cards have moved between columns yet.";
  const slowest = rows.reduce((a, b) => (b.hours > a.hours ? b : a));
  return `Cards wait longest in ${slowest.label} (median ${hours(slowest.hours)}).`;
}

export function outcomeRows(m: DevMetrics) {
  return m.run_outcomes.map((o) => ({ label: OUTCOME_LABELS[o.outcome], count: o.count }));
}

export function outcomeSummary(m: DevMetrics): string {
  const rows = outcomeRows(m);
  const total = sum(rows.map((r) => r.count));
  if (!total) return "No agent runs yet.";
  return `${total} agent runs: ${rows.map((r) => `${r.count} ${r.label.toLowerCase()}`).join(", ")}.`;
}
