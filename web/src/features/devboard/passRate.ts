import type { Schemas } from "@/lib/api";

export type PassRate = Schemas["AgentPassRateSeries"];

function sum(ns: number[]): number {
  return ns.reduce((a, b) => a + b, 0);
}

// ------------------------------------------------------------ agent pass rate

export function passRateRows(p: PassRate) {
  const byPeriod = new Map<string, { period: string; single?: number; multi?: number }>();
  for (const i of p.items) {
    const row = byPeriod.get(i.period_start) ?? { period: i.period_start };
    const rate = i.pass_rate ?? (i.runs ? i.passed / i.runs : 0);
    row[i.mode] = Math.round(rate * 100);
    byPeriod.set(i.period_start, row);
  }
  return [...byPeriod.values()].sort((a, b) => a.period.localeCompare(b.period));
}

export function passRateSummary(p: PassRate): string {
  if (!p.items.length) return "No agent runs in this range.";
  const rate = (mode: "single" | "multi") => {
    const items = p.items.filter((i) => i.mode === mode);
    const runs = sum(items.map((i) => i.runs));
    return runs
      ? `${Math.round((sum(items.map((i) => i.passed)) / runs) * 100)}% of ${runs} runs`
      : "no runs";
  };
  return `Single agent passed ${rate("single")}; multi-agent passed ${rate("multi")}.`;
}
