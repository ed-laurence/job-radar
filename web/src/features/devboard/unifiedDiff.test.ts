import { describe, expect, it } from "vitest";
import { parseUnifiedDiff } from "./unifiedDiff";

const MULTI = [
  "diff --git a/web/src/a.ts b/web/src/a.ts",
  "index 1111111..2222222 100644",
  "--- a/web/src/a.ts",
  "+++ b/web/src/a.ts",
  "@@ -1,3 +1,3 @@",
  " const a = 1;",
  "-const b = 2;",
  "+const b = 3;",
  " const c = 4;",
  "diff --git a/web/src/new.ts b/web/src/new.ts",
  "new file mode 100644",
  "index 0000000..3333333",
  "--- /dev/null",
  "+++ b/web/src/new.ts",
  "@@ -0,0 +1,2 @@",
  "+export const x = 1;",
  "+export const y = 2;",
  "diff --git a/web/src/old.ts b/web/src/old.ts",
  "deleted file mode 100644",
  "index 4444444..0000000",
  "--- a/web/src/old.ts",
  "+++ /dev/null",
  "@@ -1 +0,0 @@",
  "-gone",
  "",
].join("\n");

describe("parseUnifiedDiff", () => {
  it("splits a multi-file diff into one entry per file with its status and counts", () => {
    const files = parseUnifiedDiff(MULTI);
    expect(files.map((f) => [f.path, f.status, f.additions, f.deletions])).toEqual([
      ["web/src/a.ts", "modified", 1, 1],
      ["web/src/new.ts", "added", 2, 0],
      ["web/src/old.ts", "deleted", 0, 1],
    ]);
  });

  it("keeps hunk headers and the +/- prefix on every line", () => {
    const [first] = parseUnifiedDiff(MULTI);
    expect(first?.lines).toEqual([
      { kind: "hunk", text: "@@ -1,3 +1,3 @@" },
      { kind: "context", text: " const a = 1;" },
      { kind: "removed", text: "-const b = 2;" },
      { kind: "added", text: "+const b = 3;" },
      { kind: "context", text: " const c = 4;" },
    ]);
  });

  it("treats lines that look like file headers inside a hunk as content", () => {
    const files = parseUnifiedDiff(
      [
        "diff --git a/notes.md b/notes.md",
        "--- a/notes.md",
        "+++ b/notes.md",
        "@@ -1,2 +1,2 @@",
        "--- a heading rule",
        "+++ a plus rule",
        " tail",
      ].join("\n"),
    );
    expect(files).toHaveLength(1);
    expect(files[0]?.additions).toBe(1);
    expect(files[0]?.deletions).toBe(1);
  });

  it("reads renames, with and without content changes", () => {
    const files = parseUnifiedDiff(
      [
        "diff --git a/web/src/before.ts b/web/src/after.ts",
        "similarity index 100%",
        "rename from web/src/before.ts",
        "rename to web/src/after.ts",
        "diff --git a/web/src/x.ts b/web/src/y.ts",
        "similarity index 80%",
        "rename from web/src/x.ts",
        "rename to web/src/y.ts",
        "index 5555555..6666666 100644",
        "--- a/web/src/x.ts",
        "+++ b/web/src/y.ts",
        "@@ -1 +1 @@",
        "-old",
        "+new",
      ].join("\n"),
    );
    expect(files.map((f) => [f.oldPath, f.path, f.status, f.lines.length])).toEqual([
      ["web/src/before.ts", "web/src/after.ts", "renamed", 0],
      ["web/src/x.ts", "web/src/y.ts", "renamed", 3],
    ]);
  });

  it("marks binary files", () => {
    const files = parseUnifiedDiff(
      [
        "diff --git a/web/public/logo.png b/web/public/logo.png",
        "new file mode 100644",
        "index 0000000..7777777",
        "Binary files /dev/null and b/web/public/logo.png differ",
        "diff --git a/web/public/icon.png b/web/public/icon.png",
        "index 8888888..9999999 100644",
        "GIT binary patch",
        "literal 12",
        "zcmZ?wbhEHbWMp7uU|?VYVg?3=0LTCR",
        "",
      ].join("\n"),
    );
    expect(files.map((f) => [f.path, f.status, f.binary, f.lines.length])).toEqual([
      ["web/public/logo.png", "added", true, 0],
      ["web/public/icon.png", "modified", true, 0],
    ]);
  });

  it("keeps the no-newline-at-end marker as a note, not as content", () => {
    const [file] = parseUnifiedDiff(
      [
        "diff --git a/a.txt b/a.txt",
        "--- a/a.txt",
        "+++ b/a.txt",
        "@@ -1 +1 @@",
        "-before",
        "\\ No newline at end of file",
        "+after",
        "\\ No newline at end of file",
      ].join("\n"),
    );
    expect(file?.lines.map((l) => l.kind)).toEqual(["hunk", "removed", "note", "added", "note"]);
    expect(file?.additions).toBe(1);
    expect(file?.deletions).toBe(1);
  });

  it("handles CRLF input, an empty diff and a diff cut off mid-hunk", () => {
    expect(parseUnifiedDiff("")).toEqual([]);
    const [file] = parseUnifiedDiff(
      "diff --git a/a.txt b/a.txt\r\n--- a/a.txt\r\n+++ b/a.txt\r\n@@ -1,5 +1,5 @@\r\n-x\r\n+y",
    );
    expect(file?.lines.map((l) => l.text)).toEqual(["@@ -1,5 +1,5 @@", "-x", "+y"]);
  });

  it("parses a plain unified diff without git headers", () => {
    const files = parseUnifiedDiff(
      [
        "--- a/one.txt",
        "+++ b/one.txt",
        "@@ -1 +1 @@",
        "-a",
        "+b",
        "--- a/two.txt",
        "+++ b/two.txt",
        "@@ -1 +1 @@",
        "-c",
        "+d",
      ].join("\n"),
    );
    expect(files.map((f) => f.path)).toEqual(["one.txt", "two.txt"]);
  });
});
