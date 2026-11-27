/** Word-level and list-level diffs for proposed task edits. Small LCS, no dependency. */

export interface DiffPart {
  kind: "same" | "added" | "removed";
  text: string;
}

export type CriterionDiff =
  | { kind: "same" | "added" | "removed"; text: string }
  | { kind: "changed"; before: string; after: string };

/** Above this many LCS cells (about 4 MB) the diff is shown as a whole replacement instead. */
const MAX_CELLS = 1_000_000;

/** Words, runs of whitespace and single punctuation marks, so "search." diffs as "search" + ".". */
function tokenize(text: string): string[] {
  return text.match(/\s+|[\p{L}\p{N}_'-]+|[^\s\p{L}\p{N}_'-]/gu) ?? [];
}

type Op<T> = { kind: "same" | "added" | "removed"; item: T };

/** LCS edit script; within a change, removals come before the additions that replace them. */
function editScript<T>(a: readonly T[], b: readonly T[]): Op<T>[] | null {
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }
  const midA = a.slice(start, endA);
  const midB = b.slice(start, endB);
  const rows = midA.length;
  const cols = midB.length;
  if (rows * cols > MAX_CELLS) return null;

  // lengths[i][j] = LCS length of midA[i..] and midB[j..], stored flat.
  const width = cols + 1;
  const lengths = new Uint32Array((rows + 1) * width);
  for (let i = rows - 1; i >= 0; i--) {
    for (let j = cols - 1; j >= 0; j--) {
      lengths[i * width + j] =
        midA[i] === midB[j]
          ? (lengths[(i + 1) * width + j + 1] ?? 0) + 1
          : Math.max(lengths[(i + 1) * width + j] ?? 0, lengths[i * width + j + 1] ?? 0);
    }
  }

  const ops: Op<T>[] = a.slice(0, start).map((item) => ({ kind: "same", item }));
  let i = 0;
  let j = 0;
  while (i < rows || j < cols) {
    if (i < rows && j < cols && midA[i] === midB[j]) {
      ops.push({ kind: "same", item: midA[i] as T });
      i++;
      j++;
    } else if (
      i < rows &&
      (j >= cols || (lengths[(i + 1) * width + j] ?? 0) >= (lengths[i * width + j + 1] ?? 0))
    ) {
      ops.push({ kind: "removed", item: midA[i] as T });
      i++;
    } else {
      ops.push({ kind: "added", item: midB[j] as T });
      j++;
    }
  }
  for (const item of a.slice(endA)) ops.push({ kind: "same", item });
  return reorder(ops);
}

/** Within each run of non-equal ops, puts every removal before every addition. */
function reorder<T>(ops: Op<T>[]): Op<T>[] {
  const out: Op<T>[] = [];
  let removed: Op<T>[] = [];
  let added: Op<T>[] = [];
  const flush = () => {
    out.push(...removed, ...added);
    removed = [];
    added = [];
  };
  for (const op of ops) {
    if (op.kind === "same") {
      flush();
      out.push(op);
    } else if (op.kind === "removed") removed.push(op);
    else added.push(op);
  }
  flush();
  return out;
}

/** A word-level diff of two texts; adjacent parts of the same kind are merged. */
export function diffWords(before: string, after: string): DiffPart[] {
  const ops = editScript(tokenize(before), tokenize(after));
  if (!ops) {
    return [
      ...(before ? [{ kind: "removed" as const, text: before }] : []),
      ...(after ? [{ kind: "added" as const, text: after }] : []),
    ];
  }
  const parts: DiffPart[] = [];
  for (const op of ops) {
    const last = parts.at(-1);
    if (last?.kind === op.kind) last.text += op.item;
    else parts.push({ kind: op.kind, text: op.item });
  }
  return parts;
}

function words(text: string): Set<string> {
  return new Set(text.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? []);
}

/** Two criteria are "the same item, reworded" when at least half their words are shared. */
function similar(a: string, b: string): boolean {
  const left = words(a);
  const right = words(b);
  if (left.size === 0 || right.size === 0) return false;
  let shared = 0;
  for (const w of left) if (right.has(w)) shared++;
  return shared / Math.min(left.size, right.size) >= 0.5;
}

/**
 * The acceptance criteria as unchanged, removed, added and changed items. A removed and an added
 * item in the same gap are shown as one "changed" item when they share enough words.
 */
export function diffCriteria(before: readonly string[], after: readonly string[]): CriterionDiff[] {
  const ops = editScript(before, after) ?? [
    ...before.map((item) => ({ kind: "removed" as const, item })),
    ...after.map((item) => ({ kind: "added" as const, item })),
  ];
  const out: CriterionDiff[] = [];
  let index = 0;
  while (index < ops.length) {
    const op = ops[index] as Op<string>;
    if (op.kind === "same") {
      out.push({ kind: "same", text: op.item });
      index++;
      continue;
    }
    const removed: string[] = [];
    const added: string[] = [];
    while (index < ops.length && (ops[index] as Op<string>).kind !== "same") {
      const gap = ops[index] as Op<string>;
      (gap.kind === "removed" ? removed : added).push(gap.item);
      index++;
    }
    const pairedWith = new Map<number, string>();
    const unpaired = removed.filter((r) => {
      const match = added.findIndex((a, i) => !pairedWith.has(i) && similar(r, a));
      if (match === -1) return true;
      pairedWith.set(match, r);
      return false;
    });
    for (const text of unpaired) out.push({ kind: "removed", text });
    added.forEach((text, i) => {
      const was = pairedWith.get(i);
      out.push(
        was === undefined ? { kind: "added", text } : { kind: "changed", before: was, after: text },
      );
    });
  }
  return out;
}
