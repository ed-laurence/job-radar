import { describe, expect, it } from "vitest";
import type { Schemas } from "@/lib/api";
import { spendByFeature, spendByProvider } from "./spend";

const spend = (
  period: string,
  feature: Schemas["LlmFeature"],
  provider: Schemas["LlmProvider"],
  cost: number,
) => ({
  period_start: period,
  feature,
  provider,
  calls: 1,
  cost_aud: cost,
});

describe("spendByFeature", () => {
  it("keeps the four biggest features and folds the rest into other", () => {
    const { keys, rows } = spendByFeature({
      items: [
        spend("2026-11-10", "kit", "vertex-claude", 5),
        spend("2026-11-10", "score", "vertex-gemini", 4),
        spend("2026-11-10", "chat", "vertex-claude", 3),
        spend("2026-11-03", "research", "vertex-claude", 2),
        spend("2026-11-03", "triage", "vertex-maas", 1),
        spend("2026-11-03", "plan", "vertex-maas", 0.5),
      ],
    });
    expect(keys).toEqual(["kit", "score", "chat", "research", "other"]);
    expect(rows.map((r) => r.period)).toEqual(["2026-11-03", "2026-11-10"]);
    expect(rows[0]).toMatchObject({ research: 2, other: 1.5, kit: 0 });
  });
});

describe("spendByProvider", () => {
  it("totals cost and calls per provider, biggest first", () => {
    expect(
      spendByProvider({
        items: [
          spend("2026-11-10", "kit", "vertex-maas", 1),
          spend("2026-11-10", "kit", "vertex-claude", 2),
          spend("2026-11-03", "kit", "vertex-claude", 2),
        ],
      }),
    ).toEqual([
      { provider: "vertex-claude", cost: 4, calls: 2 },
      { provider: "vertex-maas", cost: 1, calls: 1 },
    ]);
  });
});
