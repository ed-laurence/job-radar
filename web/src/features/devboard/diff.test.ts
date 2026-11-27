import { describe, expect, it } from "vitest";
import { diffCriteria, diffWords } from "./diff";

describe("diffWords", () => {
  it("marks inserted words and keeps punctuation as its own token", () => {
    expect(diffWords("Let users search.", "Let users search contacts.")).toEqual([
      { kind: "same", text: "Let users search" },
      { kind: "added", text: " contacts" },
      { kind: "same", text: "." },
    ]);
  });

  it("puts removed words before the words that replace them", () => {
    expect(diffWords("Add search", "Add lookup")).toEqual([
      { kind: "same", text: "Add " },
      { kind: "removed", text: "search" },
      { kind: "added", text: "lookup" },
    ]);
  });

  it("handles identical and empty text", () => {
    expect(diffWords("Same text", "Same text")).toEqual([{ kind: "same", text: "Same text" }]);
    expect(diffWords("", "New")).toEqual([{ kind: "added", text: "New" }]);
    expect(diffWords("Old", "")).toEqual([{ kind: "removed", text: "Old" }]);
    expect(diffWords("", "")).toEqual([]);
  });

  it("falls back to a whole replacement for very long text", () => {
    const before = Array.from({ length: 3000 }, (_, i) => `a${i}`).join(" ");
    const after = Array.from({ length: 3000 }, (_, i) => `b${i}`).join(" ");
    expect(diffWords(before, after)).toEqual([
      { kind: "removed", text: before },
      { kind: "added", text: after },
    ]);
  });
});

describe("diffCriteria", () => {
  it("lists unchanged, changed, removed and added items", () => {
    expect(
      diffCriteria(
        ["Shows a hint", "Lists matches", "Old rule"],
        ["Shows a hint", "Lists matches newest first", "Empty search returns everything"],
      ),
    ).toEqual([
      { kind: "same", text: "Shows a hint" },
      { kind: "removed", text: "Old rule" },
      { kind: "changed", before: "Lists matches", after: "Lists matches newest first" },
      { kind: "added", text: "Empty search returns everything" },
    ]);
  });

  it("treats every item as added when there were none", () => {
    expect(diffCriteria([], ["One", "Two"])).toEqual([
      { kind: "added", text: "One" },
      { kind: "added", text: "Two" },
    ]);
  });
});
