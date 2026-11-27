import { describe, expect, it } from "vitest";
import type { Schemas } from "@/lib/api";
import { ago, poolText, scanDot, scanLine, scanResultText } from "./scan";

type Run = Schemas["ScanRun"];
type Status = Schemas["ScanStatus"];

const NOW = Date.parse("2026-11-27T02:00:00Z");
const at = (minutesAgo: number) => new Date(NOW - minutesAgo * 60_000).toISOString();

function run(over: Partial<Run> = {}): Run {
  return {
    id: "00000000-0000-4000-8000-0000000000e1",
    started_at: at(4),
    finished_at: at(3),
    trigger: "schedule",
    source: "seed",
    status: "ok",
    fetched: 5,
    new: 5,
    duplicates: 0,
    scored: 5,
    failed: 0,
    pool_remaining: 40,
    ...over,
  };
}

function status(over: Partial<Status> = {}): Status {
  return {
    last_run: run(),
    next_run_at: at(-12),
    interval_minutes: 15,
    pool_remaining: 40,
    running: false,
    ...over,
  };
}

describe("ago", () => {
  it.each([
    [0, "0m"],
    [3 * 60_000, "3m"],
    [125 * 60_000, "2h 5m"],
    [3 * 86_400_000, "3d"],
    [-5_000, "0m"],
  ])("%d ms -> %s", (ms, text) => expect(ago(ms)).toBe(text));
});

describe("scanLine", () => {
  it("reads like job-seeker: last scan and next scan", () => {
    expect(scanLine(status(), NOW)).toBe("Last scan 3m ago · next in 12 min");
  });

  it("says when no scan has run", () => {
    expect(scanLine(status({ last_run: null, next_run_at: null }), NOW)).toBe("No scans yet");
  });

  it("says a scan is running and hides the next time", () => {
    expect(scanLine(status({ running: true }), NOW)).toBe("Scanning now…");
  });

  it("never counts down below zero", () => {
    expect(scanLine(status({ next_run_at: at(2) }), NOW)).toBe("Last scan 3m ago · next in 0 min");
  });

  it("falls back to the start time of an unfinished run", () => {
    expect(scanLine(status({ last_run: run({ finished_at: null }) }), NOW)).toMatch(
      /^Last scan 4m ago/,
    );
  });
});

describe("scanDot", () => {
  it("is green when the last scan was ok, including an empty pool", () => {
    expect(scanDot(status())).toBe("ok");
    expect(scanDot(status({ last_run: run({ status: "no_new_roles", new: 0 }) }))).toBe("ok");
  });

  it("is amber when the last scan failed or none ran", () => {
    expect(scanDot(status({ last_run: run({ status: "failed" }) }))).toBe("warn");
    expect(scanDot(status({ last_run: null }))).toBe("warn");
  });

  it("is unknown before the status loads", () => {
    expect(scanDot(undefined)).toBe("unknown");
  });
});

describe("scanResultText", () => {
  it.each([
    [run({ new: 5 }), "5 new roles"],
    [run({ new: 1 }), "1 new role"],
    [run({ new: 0, status: "no_new_roles" }), "No new roles"],
    [run({ new: 0, status: "ok" }), "No new roles"],
    [run({ new: 0, status: "failed" }), "The scan failed"],
    [run({ new: 2, status: "failed" }), "2 new roles (the scan stopped early)"],
  ])("%#", (r, text) => expect(scanResultText(r)).toBe(text));
});

describe("poolText", () => {
  it("counts the roles left in the pool", () => {
    expect(poolText(40)).toBe("Pool: 40 roles left");
    expect(poolText(1)).toBe("Pool: 1 role left");
    expect(poolText(1234)).toBe("Pool: 1,234 roles left");
  });
});
