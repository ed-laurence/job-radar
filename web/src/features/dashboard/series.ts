import type { Schemas } from "@/lib/api";

export type Funnel = Schemas["FunnelSeries"];
export type PostToApply = Schemas["PostToApplySeries"];
export type Scores = Schemas["ScoreDistribution"];
export type Rejections = Schemas["RejectionReasonSeries"];

export const STATUS_LABELS: Record<Schemas["RoleStatus"], string> = {
  new: "New",
  reviewing: "Reviewing",
  applied: "Applied",
  interview: "Interview",
  offer: "Offer",
  passed: "Passed",
  closed: "Closed",
  rejected: "Rejected",
  unknown: "Unknown",
};

export const REJECTION_LABELS: Record<Schemas["RejectionCategory"], string> = {
  skills_gap: "Skills gap",
  experience_level: "Experience level",
  seniority: "Seniority",
  location: "Location",
  pay: "Pay",
  visa: "Visa",
  internal_candidate: "Internal candidate",
  role_filled: "Role filled",
  stronger_candidate: "Stronger candidate",
  culture_fit: "Culture fit",
  no_reason_given: "No reason given",
  other: "Other",
};

export function pct(n: number | undefined): string {
  return n === undefined ? "—" : `${Math.round(n * 100)}%`;
}

export function hours(n: number | undefined): string {
  if (n === undefined) return "—";
  return n >= 48 ? `${(n / 24).toFixed(1)} days` : `${n.toFixed(1)} h`;
}

function sum(ns: number[]): number {
  return ns.reduce((a, b) => a + b, 0);
}

function top<T>(items: T[], value: (t: T) => number): T | undefined {
  return items.reduce<T | undefined>(
    (best, t) => (!best || value(t) > value(best) ? t : best),
    undefined,
  );
}

// ------------------------------------------------------------ funnel

export function funnelRows(f: Funnel) {
  return f.items.map((i) => ({ label: STATUS_LABELS[i.status], count: i.count }));
}

export function funnelSummary(f: Funnel): string {
  const rows = funnelRows(f);
  if (!rows.length) return "No roles in this range.";
  return `${rows.map((r) => `${r.count} ${r.label.toLowerCase()}`).join(", ")}.`;
}

// ------------------------------------------------------------ post to apply

export function postToApplyRows(p: PostToApply) {
  return p.items.map((i) => ({
    period: i.period_start,
    median: i.median_hours ?? 0,
    count: i.count,
  }));
}

export function postToApplySummary(p: PostToApply): string {
  if (!p.items.length) return "No applications in this range.";
  const n = sum(p.items.map((i) => i.count));
  return `Median ${hours(p.median_hours)} from posting to applying (75th percentile ${hours(p.p75_hours)}) across ${n} applications.`;
}

// ------------------------------------------------------------ scores

export function scoreRows(s: Scores) {
  return s.items.map((i) => ({ label: `${i.min}–${i.max}`, count: i.count }));
}

export function scoreSummary(s: Scores): string {
  const rows = scoreRows(s);
  const total = sum(rows.map((r) => r.count));
  if (!total) return "No scored roles in this range.";
  const peak = top(rows, (r) => r.count);
  return `${total} scored roles; the most common band is ${peak?.label} with ${peak?.count}.`;
}

// ------------------------------------------------------------ rejections

export function rejectionRows(r: Rejections) {
  return [...r.items]
    .sort((a, b) => b.count - a.count)
    .map((i) => ({ label: REJECTION_LABELS[i.category], count: i.count }));
}

export function rejectionSummary(r: Rejections): string {
  const rows = rejectionRows(r);
  const total = sum(rows.map((x) => x.count));
  if (!total) return "No rejections recorded in this range.";
  return `${total} rejection reasons recorded; most often ${rows[0]?.label.toLowerCase()} (${rows[0]?.count}).`;
}

// ------------------------------------------------------------ insights

export type Insights = Schemas["JobInsights"];
export type Breakdown = Schemas["InsightBreakdown"];

const nullable = (n: number | null | undefined) => (n === null ? undefined : n);

/** The short headline for the closed disclosure, e.g. "Response rate 25%". */
export function insightsHeadline(i: Insights): string {
  const rate = nullable(i.response_rate);
  return rate === undefined ? "" : `Response rate ${pct(rate)}`;
}

export function rateTiles(i: Insights): { label: string; value: string }[] {
  const days = nullable(i.median_days_to_response);
  return [
    { label: "Response rate", value: pct(nullable(i.response_rate)) },
    { label: "Interview rate", value: pct(nullable(i.interview_rate)) },
    { label: "Offer rate", value: pct(nullable(i.offer_rate)) },
    { label: "Ghost rate", value: pct(nullable(i.ghost_rate)) },
    {
      label: "Median days to response",
      value: days === undefined ? "—" : `${days.toFixed(1)} days`,
    },
    { label: "Follow-ups on time", value: pct(nullable(i.followup_on_time_rate)) },
  ];
}

export function weeklyRows(i: Insights) {
  return i.applications_per_week.map((w) => ({ week: w.week_start, applied: w.applied }));
}

export function weeklySummary(i: Insights): string {
  const rows = weeklyRows(i);
  const total = sum(rows.map((r) => r.applied));
  if (!total) return "No applications in this range.";
  const peak = top(rows, (r) => r.applied);
  const avg = total / rows.length;
  return `${total} applications over ${rows.length} weeks, about ${avg.toFixed(1)} a week; the busiest week (starting ${peak?.week}) had ${peak?.applied}.`;
}

export const OUTCOMES = [
  { key: "found", label: "Found" },
  { key: "applied", label: "Applied" },
  { key: "interviews", label: "Interviews" },
  { key: "rejected", label: "Rejected" },
] as const;

const TRACK_LABELS: Record<string, string> = {
  contract: "Contract",
  exec: "Exec / leadership",
  mission: "Mission-driven",
};

const SOURCE_LABELS: Record<string, string> = {
  feed: "Job feed",
  manual: "Logged by hand",
  link: "Pasted link",
  seed: "Sample data",
};

export type BreakdownKind = "source" | "track" | "band";

export function breakdownLabel(kind: BreakdownKind, key: string): string {
  if (kind === "track") return TRACK_LABELS[key] ?? key;
  if (kind === "source") return SOURCE_LABELS[key] ?? key;
  return key;
}

export function breakdownRows(kind: BreakdownKind, items: Breakdown[]) {
  return items.map((b) => ({ ...b, label: breakdownLabel(kind, b.key) }));
}

/** Names the group with the best interview rate among those with any applications. */
export function breakdownSummary(kind: BreakdownKind, items: Breakdown[]): string {
  const rows = breakdownRows(kind, items);
  const applied = rows.filter((r) => r.applied > 0);
  if (!applied.length) return "No applications in this range.";
  const best = top(applied, (r) => r.interviews / r.applied);
  const rate = best ? best.interviews / best.applied : 0;
  const total = sum(applied.map((r) => r.applied));
  return `${total} applications across ${applied.length} group${applied.length === 1 ? "" : "s"}; ${best?.label} converts best, with ${pct(rate)} reaching interview.`;
}
