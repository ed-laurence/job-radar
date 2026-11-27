import { enumLabels, enumValues, type Schemas } from "@/lib/api";

export type TaskColumn = Schemas["TaskColumn"];
export type AppRole = Schemas["AppRole"];

/** Spec enum order (generated from api/openapi.yaml); also the board's left-to-right order. */
export const TASK_COLUMNS: readonly TaskColumn[] = enumValues.TaskColumn;

/** Display labels: the spec's `x-enum-labels`, generated into the client. */
export const COLUMN_LABELS: Readonly<Record<TaskColumn, string>> = enumLabels.TaskColumn;

/** Targets of the maintainer-only moves in `moveTask` (triaged→plan_review, …, ready→agent_dev). */
export const MAINTAINER_TARGETS: readonly TaskColumn[] = ["plan_review", "ready", "agent_dev"];

/** Moving into these columns starts an agent run. */
export const RUN_STARTING_TARGETS: readonly TaskColumn[] = ["plan_review", "agent_dev"];

const OUT_OF_SCOPE_TARGETS: readonly TaskColumn[] = ["out_of_scope", "backlog", "blocked"];

/**
 * Whether the UI disables a drop target up front. Only moves the spec rules out for certain are
 * locked (maintainer-only targets for a known `user`, out-of-scope cards); the server's state
 * machine decides everything else and a 422 explains it.
 */
export function isTargetLocked(
  task: { column: TaskColumn },
  to: TaskColumn,
  role: AppRole | undefined,
): boolean {
  if (to === task.column) return false;
  if (task.column === "out_of_scope" && !OUT_OF_SCOPE_TARGETS.includes(to)) return true;
  return role === "user" && MAINTAINER_TARGETS.includes(to);
}
