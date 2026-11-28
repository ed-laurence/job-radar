/** Parses a unified diff (git or plain) into one entry per file. Display only; never applied. */

export type DiffLineKind = "hunk" | "context" | "added" | "removed" | "note";

export interface DiffLine {
  kind: DiffLineKind;
  /** The raw line, prefix included, so +/- stays visible whatever the styling. */
  text: string;
}

export type FileStatus = "added" | "deleted" | "modified" | "renamed";

export interface FileDiff {
  path: string;
  /** The path before a rename. */
  oldPath?: string;
  status: FileStatus;
  binary: boolean;
  additions: number;
  deletions: number;
  lines: DiffLine[];
}

const HUNK = /^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@/;
const DEV_NULL = "/dev/null";

function stripPrefix(path: string): string {
  const trimmed = path.replace(/\t.*$/, "");
  return /^[ab]\//.test(trimmed) ? trimmed.slice(2) : trimmed;
}

/** `diff --git a/x b/y` → [x, y]. Paths with " b/" inside are ambiguous; later headers win. */
function gitPaths(line: string): [string, string] {
  const rest = line.slice("diff --git ".length);
  const split = rest.indexOf(" b/");
  if (split < 0) return [rest, rest];
  return [stripPrefix(rest.slice(0, split)), rest.slice(split + 3)];
}

function newFile(path: string): FileDiff {
  return { path, status: "modified", binary: false, additions: 0, deletions: 0, lines: [] };
}

interface Hunk {
  oldLeft: number;
  newLeft: number;
}

function hunkCounts(line: string): Hunk | null {
  const match = HUNK.exec(line);
  if (!match) return null;
  return { oldLeft: Number(match[1] ?? 1), newLeft: Number(match[2] ?? 1) };
}

/** Applies a file-header line (outside any hunk). Returns false when it isn't one. */
function applyHeader(file: FileDiff, line: string): boolean {
  if (line.startsWith("new file mode")) file.status = "added";
  else if (line.startsWith("deleted file mode")) file.status = "deleted";
  else if (line.startsWith("rename from ")) {
    file.status = "renamed";
    file.oldPath = line.slice("rename from ".length);
  } else if (line.startsWith("rename to ")) {
    file.status = "renamed";
    file.path = line.slice("rename to ".length);
  } else if (line.startsWith("Binary files ") || line === "GIT binary patch") file.binary = true;
  else if (line.startsWith("--- ")) {
    const from = line.slice(4);
    if (from === DEV_NULL) file.status = "added";
  } else if (line.startsWith("+++ ")) {
    const to = line.slice(4);
    if (to === DEV_NULL) file.status = "deleted";
    else file.path = stripPrefix(to);
  } else return false;
  return true;
}

/** Applies one line inside a hunk, counting down what the hunk header promised. */
function applyHunkLine(file: FileDiff, hunk: Hunk, line: string): void {
  const marker = line.charAt(0);
  if (marker === "+") {
    file.lines.push({ kind: "added", text: line });
    file.additions += 1;
    hunk.newLeft -= 1;
  } else if (marker === "-") {
    file.lines.push({ kind: "removed", text: line });
    file.deletions += 1;
    hunk.oldLeft -= 1;
  } else if (marker === "\\") {
    file.lines.push({ kind: "note", text: line });
  } else {
    // A blank line is context whose single space was stripped in transit.
    file.lines.push({ kind: "context", text: line });
    hunk.oldLeft -= 1;
    hunk.newLeft -= 1;
  }
}

export function parseUnifiedDiff(diff: string): FileDiff[] {
  const files: FileDiff[] = [];
  let file: FileDiff | null = null;
  let hunk: Hunk | null = null;
  const lines = diff.split(/\r?\n/);
  if (lines.at(-1) === "") lines.pop();

  for (const line of lines) {
    const inHunk = hunk !== null && (hunk.oldLeft > 0 || hunk.newLeft > 0);
    if (inHunk && file && hunk && !line.startsWith("diff --git ")) {
      applyHunkLine(file, hunk, line);
      continue;
    }
    // A "\ No newline" marker may follow the hunk's last counted line.
    if (file && hunk && line.startsWith("\\")) {
      file.lines.push({ kind: "note", text: line });
      continue;
    }
    hunk = null;
    if (line.startsWith("diff --git ")) {
      const [from, to] = gitPaths(line);
      file = newFile(to);
      if (from !== to) file.oldPath = from;
      files.push(file);
      continue;
    }
    // A plain diff starts each file at its "---" header.
    if (line.startsWith("--- ") && (!file || file.lines.length > 0)) {
      file = newFile(stripPrefix(line.slice(4)));
      files.push(file);
    }
    if (!file) continue;
    const counts = hunkCounts(line);
    if (counts) {
      hunk = counts;
      file.lines.push({ kind: "hunk", text: line });
      continue;
    }
    applyHeader(file, line);
  }
  return files;
}
