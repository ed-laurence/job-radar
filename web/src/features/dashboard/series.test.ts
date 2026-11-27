import { describe, expect, it } from "vitest";
import {
  breakdownSummary,
  hours,
  type Insights,
  insightsHeadline,
  rateTiles,
  weeklySummary,
} from "./series";

describe("hours", () => {
  it("switches to days past two days", () => {
    expect(hours(undefined)).toBe("—");
    expect(hours(5)).toBe("5.0 h");
    expect(hours(72)).toBe("3.0 days");
  });
});

const EMPTY: Insights = {
  applications_per_week: [],
  by_source: [],
  by_track: [],
  score_bands: [],
  as_of: "2026-11-25T01:00:00Z",
};

describe("insights", () => {
  it("headlines the response rate only when there is one", () => {
    expect(insightsHeadline(EMPTY)).toBe("");
    expect(insightsHeadline({ ...EMPTY, response_rate: 0.333 })).toBe("Response rate 33%");
  });

  it("shows a dash for missing rates", () => {
    expect(rateTiles({ ...EMPTY, ghost_rate: null }).map((t) => t.value)).toEqual([
      "—",
      "—",
      "—",
      "—",
      "—",
      "—",
    ]);
  });

  it("summarises weeks and breakdowns, and says so when empty", () => {
    expect(weeklySummary(EMPTY)).toBe("No applications in this range.");
    expect(
      weeklySummary({
        ...EMPTY,
        applications_per_week: [
          { week_start: "2026-11-09", applied: 1 },
          { week_start: "2026-11-16", applied: 3 },
        ],
      }),
    ).toBe(
      "4 applications over 2 weeks, about 2.0 a week; the busiest week (starting 2026-11-16) had 3.",
    );
    expect(breakdownSummary("track", [])).toBe("No applications in this range.");
    expect(
      breakdownSummary("track", [
        { key: "exec", found: 9, applied: 4, interviews: 1, rejected: 0 },
        { key: "mission", found: 0, applied: 0, interviews: 0, rejected: 0 },
      ]),
    ).toBe(
      "4 applications across 1 group; Exec / leadership converts best, with 25% reaching interview.",
    );
  });
});
