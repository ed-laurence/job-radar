import { describe, expect, it } from "vitest";
import type { Schemas } from "@/lib/api";
import { ago, cardActions, clock, heat, scoreBand } from "./card";
import { visibleTabs } from "./tabs";

const NOW = Date.parse("2026-11-27T12:00:00Z");
const role = (over: Partial<Schemas["Role"]>): Schemas["Role"] => ({
  id: "00000000-0000-4000-8000-0000000000a1",
  source: "feed",
  title: "Data Engineer",
  stage: "scored",
  status: "new",
  found_at: "2026-11-27T09:00:00Z",
  rank: "a0",
  version: 1,
  created_at: "2026-11-27T09:00:00Z",
  updated_at: "2026-11-27T09:00:00Z",
  ...over,
});

describe("scoreBand", () => {
  it("bands at 75 and 55", () => {
    expect(scoreBand(75)).toBe("high");
    expect(scoreBand(74)).toBe("mid");
    expect(scoreBand(55)).toBe("mid");
    expect(scoreBand(54)).toBe("low");
    expect(scoreBand(undefined)).toBe("none");
  });
});

describe("clock", () => {
  it("is hot under an hour and warm under four", () => {
    expect(heat(59 * 60_000)).toBe("hot");
    expect(heat(3 * 3_600_000)).toBe("warm");
    expect(heat(5 * 3_600_000)).toBe("cold");
  });

  it("prefers the posting time and falls back to when it was found", () => {
    expect(clock(role({ posted_at: "2026-11-27T11:30:00Z" }), NOW)).toEqual({
      text: "Posted 30m ago",
      heat: "hot",
    });
    expect(clock(role({}), NOW)).toEqual({ text: "Found 3h ago", heat: "warm" });
    expect(ago(3 * 86_400_000)).toBe("3d");
  });
});

describe("cardActions", () => {
  it("offers apply and pass while acting, and a way back otherwise", () => {
    expect(cardActions("new")).toEqual(["applied", "passed"]);
    expect(cardActions("passed")).toEqual(["applied", "new"]);
    expect(cardActions("applied")).toEqual(["new"]);
    expect(cardActions("rejected")).toEqual(["new"]);
  });
});

describe("visibleTabs", () => {
  const counts = {
    act_now: 1,
    applied: 0,
    interviewing: 0,
    passed: 0,
    rejected: 0,
    unknown: 0,
    filtered: 0,
    outreach: 0,
    feedback: 0,
  };
  it("hides Filtered until it has roles", () => {
    expect(visibleTabs(counts)).not.toContain("filtered");
    expect(visibleTabs({ ...counts, filtered: 2 })).toContain("filtered");
  });

  it("always shows Outreach and Feedback, and Campaign only without the campaign column", () => {
    expect(visibleTabs(counts).slice(-3)).toEqual(["outreach", "feedback", "campaign"]);
    expect(visibleTabs(counts, true)).not.toContain("campaign");
  });
});
