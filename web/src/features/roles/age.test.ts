import { describe, expect, it } from "vitest";
import {
  ageDays,
  ageError,
  ageLabel,
  ageQuery,
  parseAge,
  presetOf,
  rangeOf,
  withAgeParams,
} from "./age";

describe("role age filter", () => {
  it("maps presets to ranges and back", () => {
    expect(rangeOf("1")).toEqual({ min: "", max: 1 });
    expect(rangeOf("older30")).toEqual({ min: 30, max: "" });
    expect(rangeOf("custom")).toBeUndefined();
    expect(presetOf({ min: "", max: 14 })).toBe("14");
    expect(presetOf({ min: "", max: "" })).toBe("any");
    expect(presetOf({ min: 2, max: 9 })).toBe("custom");
  });

  it("parses the URL, ignoring malformed values", () => {
    expect(parseAge(new URLSearchParams("min_age_days=3&max_age_days=10"))).toEqual({
      min: 3,
      max: 10,
    });
    expect(parseAge({ min_age_days: "-1", max_age_days: ["7", "9"] })).toEqual({ min: "", max: 7 });
    expect(parseAge({ max_age_days: "99999" })).toEqual({ min: "", max: "" });
  });

  it("only queries a valid range", () => {
    expect(ageQuery({ min: "", max: 7 })).toEqual({ max_age_days: 7 });
    expect(ageQuery({ min: 30, max: "" })).toEqual({ min_age_days: 30 });
    expect(ageError({ min: 10, max: 5 })).toMatch(/no more than/);
    expect(ageQuery({ min: 10, max: 5 })).toEqual({});
  });

  it("replaces the age parameters and keeps the others", () => {
    expect(withAgeParams("?tab=x&max_age_days=3", { min: "", max: 7 })).toBe(
      "?tab=x&max_age_days=7",
    );
    expect(withAgeParams("?max_age_days=3", { min: "", max: "" })).toBe("");
  });
});

describe("role age column", () => {
  const NOW = Date.parse("2026-11-27T12:00:00Z");

  it("counts whole days since posted, else since found", () => {
    expect(
      ageDays({ posted_at: "2026-11-20T12:00:00Z", found_at: "2026-11-26T00:00:00Z" }, NOW),
    ).toBe(7);
    expect(ageDays({ found_at: "2026-11-25T13:00:00Z" }, NOW)).toBe(1);
    expect(ageDays({ found_at: "not a date" }, NOW)).toBeNull();
  });

  it("labels today, one day and many days; never negative", () => {
    expect(ageLabel({ found_at: "2026-11-27T08:00:00Z" }, NOW)).toBe("Today");
    expect(ageLabel({ found_at: "2026-11-26T08:00:00Z" }, NOW)).toBe("1 day");
    expect(
      ageLabel({ posted_at: "2026-10-28T12:00:00Z", found_at: "2026-11-01T00:00:00Z" }, NOW),
    ).toBe("30 days");
    expect(ageLabel({ found_at: "2026-11-28T08:00:00Z" }, NOW)).toBe("Today");
    expect(ageLabel({ found_at: "" }, NOW)).toBe("—");
  });
});
