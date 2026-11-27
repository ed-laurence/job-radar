import { describe, expect, it } from "vitest";
import { uncoveredTestChanges } from "./testChanges";

describe("uncoveredTestChanges", () => {
  const paths = ["web/tests/e2e/board.spec.ts", "backend/tests/unit/test_board.py"];

  it("returns every path when the card has no modify-tests label", () => {
    expect(uncoveredTestChanges(paths, ["area:web"])).toEqual(paths);
  });

  it("returns nothing when the plan changes no tests", () => {
    expect(uncoveredTestChanges([], [])).toEqual([]);
  });

  it("treats an exact path as covered", () => {
    expect(uncoveredTestChanges(paths, ["modify-tests:web/tests/e2e/board.spec.ts"])).toEqual([
      "backend/tests/unit/test_board.py",
    ]);
  });

  it("splits comma-separated paths and trims spaces", () => {
    expect(
      uncoveredTestChanges(paths, [
        "modify-tests: web/tests/e2e/board.spec.ts , backend/tests/unit/test_board.py",
      ]),
    ).toEqual([]);
  });

  it("lets a directory prefix cover the files under it, with or without a trailing slash", () => {
    expect(
      uncoveredTestChanges(paths, ["modify-tests:web/tests/", "modify-tests:backend/tests"]),
    ).toEqual([]);
  });

  it("does not treat a partial segment as a directory prefix", () => {
    expect(uncoveredTestChanges(["web/tests-old/a.spec.ts"], ["modify-tests:web/tests"])).toEqual([
      "web/tests-old/a.spec.ts",
    ]);
  });

  it("ignores other labels and empty label values", () => {
    expect(
      uncoveredTestChanges(paths, ["allow:web/tests", "modify-tests:", "modify-tests:,"]),
    ).toEqual(paths);
  });
});
