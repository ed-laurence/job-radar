import { describe, expect, it } from "vitest";
import { formatDateTime, formatShortDateTime, isValidTimeZone } from "./format";

describe("isValidTimeZone", () => {
  it.each([
    ["Australia/Melbourne", true],
    ["UTC", true],
    ["Mars/Olympus_Mons", false],
    ["", false],
  ])("%s -> %s", (zone, ok) => {
    expect(isValidTimeZone(zone)).toBe(ok);
  });
});

describe("formatDateTime", () => {
  it("shows the time in the chosen zone", () => {
    const iso = "2026-11-25T01:00:00Z";
    expect(formatDateTime(iso, "UTC")).toMatch(/1:00/);
    expect(formatDateTime(iso, "Australia/Melbourne")).toMatch(/12:00/);
  });

  it("falls back to the browser zone for an unknown zone, and blanks a bad date", () => {
    expect(formatDateTime("2026-11-25T01:00:00Z", "Nowhere/Here")).not.toBe("");
    expect(formatDateTime("not a date")).toBe("");
  });
});

describe("formatShortDateTime", () => {
  it("reads like job-seeker's 'Checked' line, in the chosen zone", () => {
    const iso = "2026-11-25T20:02:00Z";
    expect(formatShortDateTime(iso, "Australia/Melbourne")).toBe("Thu, 26 Nov, 7:02 am");
    expect(formatShortDateTime(iso, "UTC")).toBe("Wed, 25 Nov, 8:02 pm");
  });

  it("blanks a bad date", () => {
    expect(formatShortDateTime("not a date")).toBe("");
  });
});
