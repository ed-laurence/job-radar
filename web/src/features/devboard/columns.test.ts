import { describe, expect, it } from "vitest";
import { dropStateFor, isTargetLocked, MAINTAINER_TARGETS } from "./columns";

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

  it("uses the server's allowed columns when the card carries them", () => {
    const card = { column: "backlog" as const, allowed_columns: ["blocked" as const] };
    // Backlog -> Done isn't a legal move, so that column is red even for the maintainer.
    expect(dropStateFor(card, "done", "maintainer")).toBe("invalid");
    expect(dropStateFor(card, "blocked", "maintainer")).toBe("valid");
    expect(dropStateFor(card, "backlog", "maintainer")).toBe("valid"); // a reorder
    expect(isTargetLocked(card, "done", "maintainer")).toBe(true);
    expect(isTargetLocked(card, "blocked", "user")).toBe(false);
  });

  it("derives a column's drop state from the same locking rules", () => {
    expect(dropStateFor(undefined, "ready", "user")).toBe("idle");
    expect(dropStateFor({ column: "triaged" }, "ready", "user")).toBe("invalid");
    expect(dropStateFor({ column: "triaged" }, "ready", "maintainer")).toBe("valid");
    expect(dropStateFor({ column: "triaged" }, "triaged", "user")).toBe("valid");
    expect(dropStateFor({ column: "out_of_scope" }, "done", "maintainer")).toBe("invalid");
  });
});
