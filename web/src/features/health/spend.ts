import type { Schemas } from "@/lib/api";
import { money } from "./health";

export type Spend = Schemas["LlmSpendSeries"];

/** Spend series amounts are AUD (`cost_aud`); same en-AU format as the health stats. */
export const aud = (n: number): string => money(n);

function sum(ns: number[]): number {
  return ns.reduce((a, b) => a + b, 0);
}

// ------------------------------------------------------------ LLM spend

const MAX_SERIES = 4;

/** Spend per period with the top features as series and the rest folded into "other". */
export function spendByFeature(s: Spend): {
  keys: string[];
  rows: Record<string, number | string>[];
} {
  const totals = new Map<string, number>();
  for (const i of s.items) totals.set(i.feature, (totals.get(i.feature) ?? 0) + i.cost_aud);
  const ranked = [...totals.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k);
  const shown = ranked.slice(0, MAX_SERIES);
  const keys = ranked.length > MAX_SERIES ? [...shown, "other"] : shown;
  const byPeriod = new Map<string, Record<string, number | string>>();
  for (const i of s.items) {
    const row = byPeriod.get(i.period_start) ?? {
      period: i.period_start,
      ...Object.fromEntries(keys.map((k) => [k, 0])),
    };
    const key = shown.includes(i.feature) ? i.feature : "other";
    row[key] = Number(row[key] ?? 0) + i.cost_aud;
    byPeriod.set(i.period_start, row);
  }
  const rows = [...byPeriod.values()].sort((a, b) =>
    String(a.period).localeCompare(String(b.period)),
  );
  return { keys, rows };
}

export function spendByProvider(s: Spend) {
  const totals = new Map<string, { cost: number; calls: number }>();
  for (const i of s.items) {
    const t = totals.get(i.provider) ?? { cost: 0, calls: 0 };
    totals.set(i.provider, { cost: t.cost + i.cost_aud, calls: t.calls + i.calls });
  }
  return [...totals.entries()]
    .map(([provider, t]) => ({ provider, cost: t.cost, calls: t.calls }))
    .sort((a, b) => b.cost - a.cost);
}

export function spendFeatureSummary(s: Spend): string {
  const { keys, rows } = spendByFeature(s);
  if (!rows.length) return "No LLM spend in this range.";
  const total = sum(s.items.map((i) => i.cost_aud));
  return `${aud(total)} spent across ${rows.length} periods; the biggest feature is ${keys[0]}.`;
}

export function spendProviderSummary(s: Spend): string {
  const rows = spendByProvider(s);
  if (!rows.length) return "No LLM spend in this range.";
  return `${rows.map((r) => `${r.provider} ${aud(r.cost)} over ${r.calls} calls`).join("; ")}.`;
}
