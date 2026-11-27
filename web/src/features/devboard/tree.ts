import type { Plan, Run } from "./api";

export interface RunTreeNode {
  run: Run;
  children: RunTreeNode[];
}

/**
 * Builds the run tree from `parent_run_id` (lead → writers → publisher → reviewers). Roots are
 * newest first; children run in creation order. A run whose parent isn't listed becomes a root.
 */
export function buildRunTree(runs: readonly Run[]): RunTreeNode[] {
  const nodes = new Map(runs.map((run) => [run.id, { run, children: [] as RunTreeNode[] }]));
  const roots: RunTreeNode[] = [];
  for (const node of nodes.values()) {
    const parentId = node.run.parent_run_id;
    const parent = parentId && parentId !== node.run.id ? nodes.get(parentId) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  const byCreated = (a: RunTreeNode, b: RunTreeNode) =>
    a.run.created_at < b.run.created_at ? -1 : a.run.created_at > b.run.created_at ? 1 : 0;
  for (const node of nodes.values()) node.children.sort(byCreated);
  return roots.sort((a, b) => byCreated(b, a));
}

/** Lease paths per agent, from the plan's subtasks. */
export function leasesByAgent(plan: Plan | null | undefined): Map<string, string[]> {
  const map = new Map<string, string[]>();
  for (const s of plan?.subtasks ?? []) {
    map.set(s.agent, [...(map.get(s.agent) ?? []), ...s.lease_paths]);
  }
  return map;
}
