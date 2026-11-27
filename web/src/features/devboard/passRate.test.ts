import { describe, expect, it } from "vitest";
import { passRateRows } from "./passRate";

describe("passRateRows", () => {
  it("pivots modes into one row per period, deriving a missing rate", () => {
    expect(
      passRateRows({
        items: [
          { period_start: "2026-11-10", mode: "single", runs: 4, passed: 1 },
          { period_start: "2026-11-10", mode: "multi", runs: 2, passed: 2, pass_rate: 1 },
        ],
      }),
    ).toEqual([{ period: "2026-11-10", single: 25, multi: 100 }]);
  });
});
