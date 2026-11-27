import { describe, expect, it } from "vitest";
import {
  budgetText,
  formatVital,
  type Job,
  jobName,
  jobsHealth,
  jobsHealthText,
  money,
  vitalStatus,
  vitalsSummary,
} from "./health";

const job = (name: string, last_status: Job["last_status"]): Job => ({ name, last_status });

describe("jobsHealth", () => {
  it("is ok only when every job last succeeded", () => {
    expect(jobsHealth([job("digest", "ok"), job("rollup", "ok")])).toBe("ok");
    expect(jobsHealthText([job("digest", "ok")])).toBe("Scheduled jobs: all OK");
  });

  it("warns when any job failed or never ran", () => {
    expect(jobsHealth([job("digest", "ok"), job("rollup", "failed")])).toBe("warn");
    expect(jobsHealth([job("watchdog", "never")])).toBe("warn");
    expect(jobsHealthText([job("a", "failed"), job("b", "never")])).toBe(
      "Scheduled jobs: 2 need attention",
    );
  });

  it("is unknown with no data", () => {
    expect(jobsHealth(undefined)).toBe("unknown");
    expect(jobsHealth([])).toBe("unknown");
  });
});

describe("formatting", () => {
  it("names jobs as words", () => {
    expect(jobName("feed_replay")).toBe("Feed replay");
  });

  it("formats vitals and budgets", () => {
    expect(formatVital("CLS", 0.12345)).toBe("0.123");
    expect(formatVital("LCP", 2512.4)).toBe("2,512 ms");
    expect(vitalStatus({ metric: "LCP", value: 3000, budget: 2500 })).toBe("over");
    expect(vitalStatus({ metric: "INP", value: 100, budget: null })).toBe("none");
    expect(vitalsSummary([{ metric: "LCP", value: 3000, budget: 2500 }])).toBe(
      "1 of 1 Web Vitals over budget at p75: LCP 3,000 ms (budget 2,500 ms).",
    );
    expect(budgetText(12.4, 50)).toBe("$12.40 of $50.00 (25%)");
    expect(budgetText(3, null)).toBe("$3.00 (no budget set)");
    expect(budgetText(1234.5, 10_000)).toBe("$1,234.50 of $10,000.00 (12%)");
  });

  it("formats money as AUD in en-AU, with a dash when absent", () => {
    expect(money(1.25)).toBe("$1.25");
    expect(money(0)).toBe("$0.00");
    expect(money(1234.567)).toBe("$1,234.57");
    expect(money(null)).toBe("—");
    expect(money(undefined)).toBe("—");
    expect(money(Number.NaN)).toBe("—");
  });
});
