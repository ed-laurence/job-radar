import type { Schemas } from "@/lib/api";

export type HealthSummary = Schemas["HealthSummary"];
export type Job = HealthSummary["jobs"][number];
export type Vital = HealthSummary["site"]["web_vitals_p75"][number];

/** The nav dot: `ok` when every scheduled job last succeeded, `warn` when any failed or never ran. */
export type JobsHealth = "ok" | "warn" | "unknown";

export function jobsHealth(jobs: readonly Job[] | undefined): JobsHealth {
  if (!jobs || jobs.length === 0) return "unknown";
  return jobs.every((j) => j.last_status === "ok") ? "ok" : "warn";
}

export function jobsHealthText(jobs: readonly Job[] | undefined): string {
  const state = jobsHealth(jobs);
  if (state === "unknown") return "Scheduled jobs: status unknown";
  if (state === "ok") return "Scheduled jobs: all OK";
  const bad = (jobs ?? []).filter((j) => j.last_status !== "ok").length;
  return `Scheduled jobs: ${bad} need${bad === 1 ? "s" : ""} attention`;
}

export const JOB_STATUS_TEXT: Readonly<Record<Job["last_status"], string>> = {
  ok: "OK",
  failed: "Failed",
  never: "Never ran",
};

/** Job names are snake_case identifiers; show them as words. */
export function jobName(name: string): string {
  const words = name.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : `${d.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

const count = new Intl.NumberFormat("en");

const aud = new Intl.NumberFormat("en-AU", { style: "currency", currency: "AUD" });

/** An AUD amount in en-AU (shown as "$1,234.50"); a dash when absent. */
export function money(n: number | null | undefined): string {
  return typeof n === "number" && Number.isFinite(n) ? aud.format(n) : "—";
}

export function rate(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : `${Math.round(n * 100)}%`;
}

export function whole(n: number): string {
  return count.format(n);
}

export function formatVital(metric: Vital["metric"], value: number): string {
  if (metric === "CLS") return value.toFixed(3);
  return `${count.format(Math.round(value))} ms`;
}

export type VitalStatus = "over" | "within" | "none";

export function vitalStatus(v: Vital): VitalStatus {
  if (v.budget === null || v.budget === undefined) return "none";
  return v.value > v.budget ? "over" : "within";
}

export function vitalsSummary(vitals: readonly Vital[]): string {
  if (vitals.length === 0) return "No Web Vitals samples in the last 7 days.";
  const over = vitals.filter((v) => vitalStatus(v) === "over");
  if (over.length === 0) return `All ${vitals.length} Web Vitals are within budget at p75.`;
  const named = over.map(
    (v) =>
      `${v.metric} ${formatVital(v.metric, v.value)} (budget ${formatVital(v.metric, v.budget ?? 0)})`,
  );
  return `${over.length} of ${vitals.length} Web Vitals over budget at p75: ${named.join("; ")}.`;
}

/** Month spend against the budget, e.g. "$12.40 of $50.00 (25%)". */
export function budgetText(spent: number, budget: number | null | undefined): string {
  if (budget === null || budget === undefined || budget <= 0) {
    return `${money(spent)} (no budget set)`;
  }
  return `${money(spent)} of ${money(budget)} (${Math.round((spent / budget) * 100)}%)`;
}
