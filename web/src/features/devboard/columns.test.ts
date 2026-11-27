import { describe, expect, it } from "vitest";
import { isTargetLocked, MAINTAINER_TARGETS } from "./columns";

describe("columns", () => {
  it("locks maintainer-only targets for the user role only", () => {
    for (const to of MAINTAINER_TARGETS) {
      expect(isTargetLocked({ column: "triaged" }, to, "user")).toBe(true);
      expect(isTargetLocked({ column: "triaged" }, to, "maintainer")).toBe(false);
    }
    expect(isTargetLocked({ column: "triaged" }, "backlog", "user")).toBe(false);
    // Unknown role: nothing is pre-disabled; the server decides.
    expect(isTargetLocked({ column: "triaged" }, "ready", undefined)).toBe(false);
  });

  it("locks out-of-scope cards to backlog, blocked or a reorder", () => {
    const card = { column: "out_of_scope" as const };
    expect(isTargetLocked(card, "backlog", "maintainer")).toBe(false);
    expect(isTargetLocked(card, "blocked", "maintainer")).toBe(false);
    expect(isTargetLocked(card, "out_of_scope", "maintainer")).toBe(false);
    expect(isTargetLocked(card, "ready", "maintainer")).toBe(true);
    expect(isTargetLocked(card, "triaged", "maintainer")).toBe(true);
  });
});
