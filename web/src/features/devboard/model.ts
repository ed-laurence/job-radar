import { isApiError, type Schemas } from "@/lib/api";
import { COLUMN_LABELS, TASK_COLUMNS, type TaskColumn } from "./columns";
import { rankBetween } from "./rank";

export type Task = Schemas["Task"];

const COLUMN_PREFIX = "column:";

/** dnd-kit id of a column's drop zone (card ids are task ids). */
export function columnDropId(column: TaskColumn): string {
  return `${COLUMN_PREFIX}${column}`;
}

export function columnFromDropId(id: string): TaskColumn | undefined {
  if (!id.startsWith(COLUMN_PREFIX)) return undefined;
  const column = id.slice(COLUMN_PREFIX.length);
  return TASK_COLUMNS.find((c) => c === column);
}

/** Byte-order comparison, matching the server's `COLLATE "C"` (not `localeCompare`). */
function byRank(a: Task, b: Task): number {
  if (a.rank === b.rank) return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  return a.rank < b.rank ? -1 : 1;
}

export function groupByColumn(tasks: readonly Task[]): Record<TaskColumn, Task[]> {
  const grouped = Object.fromEntries(TASK_COLUMNS.map((c) => [c, [] as Task[]])) as Record<
    TaskColumn,
    Task[]
  >;
  for (const t of tasks) grouped[t.column].push(t);
  for (const c of TASK_COLUMNS) grouped[c].sort(byRank);
  return grouped;
}

export interface Drop {
  toColumn: TaskColumn;
  rank: string;
}

/**
 * Where a drag ends: the target column and a rank between the new neighbours.
 * `overId` is a card id or a column drop id. Returns null for a no-op.
 */
export function resolveDrop(
  tasks: readonly Task[],
  activeId: string,
  overId: string | null,
): Drop | null {
  if (overId === null || overId === activeId) return null;
  const active = tasks.find((t) => t.id === activeId);
  if (!active) return null;
  const grouped = groupByColumn(tasks);

  const overColumn = columnFromDropId(overId);
  if (overColumn) {
    if (overColumn === active.column) return null;
    const last = grouped[overColumn].at(-1);
    const rank = rankBetween(last?.rank ?? null, null);
    return rank ? { toColumn: overColumn, rank } : null;
  }

  const over = tasks.find((t) => t.id === overId);
  if (!over) return null;
  const original = grouped[over.column];
  const others = original.filter((t) => t.id !== activeId);
  let index = others.findIndex((t) => t.id === overId);
  // Sortable semantics: moving down within a column lands after the card it is over.
  if (
    active.column === over.column &&
    original.indexOf(active) < original.findIndex((t) => t.id === overId)
  ) {
    index += 1;
  }
  const rank = rankBetween(others[index - 1]?.rank ?? null, others[index]?.rank ?? null);
  return rank ? { toColumn: over.column, rank } : null;
}

export function applyMove(
  tasks: readonly Task[],
  id: string,
  toColumn: TaskColumn,
  rank: string,
): Task[] {
  return tasks.map((t) => (t.id === id ? { ...t, column: toColumn, rank } : t));
}

function columnList(columns: readonly TaskColumn[]): string {
  return columns.map((c) => COLUMN_LABELS[c]).join(", ");
}

/** A user-facing explanation of a failed move, built only from the problem details (SEC-07). */
export function moveErrorMessage(error: unknown, task: Task, to: TaskColumn): string {
  const name = `“${task.title}”`;
  if (isApiError(error)) {
    const { status, problem } = error;
    if (status === 409) {
      return `${name} changed elsewhere, so the move was undone and the board refreshed. Try again.`;
    }
    if (status === 403) {
      return `Only a maintainer can move a card to ${COLUMN_LABELS[to]}.`;
    }
    if (status === 422 && problem.type === "/problems/plan-not-approved") {
      return `Approve the plan and tests for ${name} before moving it to ${COLUMN_LABELS[to]}.`;
    }
    if (status === 422) {
      const parts = [
        `Can't move ${name} from ${COLUMN_LABELS[task.column]} to ${COLUMN_LABELS[to]}.`,
      ];
      if (problem.detail) parts.push(problem.detail);
      if (problem.allowed_columns?.length) {
        parts.push(`Allowed: ${columnList(problem.allowed_columns)}.`);
      }
      return parts.join(" ");
    }
  }
  const reference =
    isApiError(error) && error.correlationId ? ` Reference: ${error.correlationId}` : "";
  return `Couldn't move ${name}. Try again.${reference}`;
}
