"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAnalytics } from "@/lib/AnalyticsProvider";
import { isApiError, type Schemas, unwrap, useApiClient } from "@/lib/api";
import { RUN_STARTING_TARGETS, type TaskColumn } from "./columns";
import type { Task } from "./model";

export type TriageResult = Schemas["TriageResult"];
export type Plan = Schemas["Plan"];
export type Run = Schemas["Run"];
export type TaskCreate = Schemas["TaskCreate"];
export type TaskUpdate = Schemas["TaskUpdate"];

const MAX_PAGES = 10;
const ACTIVE_RUN_POLL_MS = 5000;
/** The board is shared and the system moves cards too (triage, reviews, merges). */
const BOARD_POLL_MS = 15_000;

export const devboardKeys = {
  all: ["devboard"] as const,
  tasks: () => ["devboard", "tasks"] as const,
  task: (id: string) => ["devboard", "task", id] as const,
  triage: (id: string) => ["devboard", "task", id, "triage"] as const,
  plan: (id: string) => ["devboard", "task", id, "plan"] as const,
  runs: (id: string) => ["devboard", "task", id, "runs"] as const,
  /** Outside the task prefix, so refreshing the card never refetches a chat mid-reply. */
  chat: (id: string) => ["devboard", "chat", id] as const,
};

/** 404 means "not there yet" (no triage or plan so far), not an error. */
async function orNull<T>(request: Promise<T>): Promise<T | null> {
  try {
    return await request;
  } catch (error) {
    if (isApiError(error) && error.status === 404) return null;
    throw error;
  }
}

export function useTasks() {
  const client = useApiClient();
  return useQuery({
    queryKey: devboardKeys.tasks(),
    queryFn: async () => {
      const items: Task[] = [];
      let cursor: string | undefined;
      for (let page = 0; page < MAX_PAGES; page++) {
        const data = await unwrap(
          client.GET("/tasks", {
            params: { query: { limit: 100, ...(cursor ? { cursor } : {}) } },
          }),
        );
        items.push(...data.items);
        cursor = data.next_cursor;
        if (!cursor) break;
      }
      return items;
    },
    refetchInterval: BOARD_POLL_MS,
  });
}

export function useTask(taskId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: devboardKeys.task(taskId),
    queryFn: () => unwrap(client.GET("/tasks/{taskId}", { params: { path: { taskId } } })),
  });
}

export function useTriage(taskId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: devboardKeys.triage(taskId),
    queryFn: () =>
      orNull(unwrap(client.GET("/tasks/{taskId}/triage", { params: { path: { taskId } } }))),
  });
}

export function usePlan(taskId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: devboardKeys.plan(taskId),
    queryFn: () =>
      orNull(unwrap(client.GET("/tasks/{taskId}/plan", { params: { path: { taskId } } }))),
  });
}

const ACTIVE: readonly Run["status"][] = ["queued", "running"];

export function isActiveRun(run: Run): boolean {
  return ACTIVE.includes(run.status);
}

export function useRuns(taskId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: devboardKeys.runs(taskId),
    queryFn: async () => {
      const data = await unwrap(
        client.GET("/tasks/{taskId}/runs", {
          params: { path: { taskId }, query: { limit: 100 } },
        }),
      );
      return data.items;
    },
    // Status, cost and new child runs change while a run is active; steps stream separately.
    refetchInterval: (query) => (query.state.data?.some(isActiveRun) ? ACTIVE_RUN_POLL_MS : false),
  });
}

/** Refreshes everything about one card (after a run finishes, a conflict, …). */
export function useRefreshTask(taskId: string) {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: devboardKeys.task(taskId) }),
      queryClient.invalidateQueries({ queryKey: devboardKeys.tasks() }),
    ]);
}

export interface MoveVariables {
  task: Task;
  toColumn: TaskColumn;
  rank: string;
  reason?: string;
}

/**
 * Optimistic move: the card jumps at once and is rolled back on any error. A 409 also
 * refetches the board, since the card changed elsewhere.
 */
export function useMoveTask() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const { track } = useAnalytics();
  return useMutation({
    mutationFn: ({ task, toColumn, rank, reason }: MoveVariables) =>
      unwrap(
        client.POST("/tasks/{taskId}/move", {
          params: { path: { taskId: task.id } },
          body: { to_column: toColumn, rank, version: task.version, ...(reason ? { reason } : {}) },
        }),
      ),
    onMutate: async ({ task, toColumn, rank }) => {
      await queryClient.cancelQueries({ queryKey: devboardKeys.tasks() });
      const previous = queryClient.getQueryData<Task[]>(devboardKeys.tasks());
      queryClient.setQueryData<Task[]>(devboardKeys.tasks(), (old) =>
        old?.map((t) => (t.id === task.id ? { ...t, column: toColumn, rank } : t)),
      );
      return { previous };
    },
    onError: async (error, _vars, context) => {
      if (context?.previous) queryClient.setQueryData(devboardKeys.tasks(), context.previous);
      if (isApiError(error) && error.status === 409) {
        await queryClient.invalidateQueries({ queryKey: devboardKeys.tasks() });
      }
    },
    onSuccess: (moved, { task }) => {
      queryClient.setQueryData<Task[]>(devboardKeys.tasks(), (old) =>
        old?.map((t) => (t.id === moved.id ? moved : t)),
      );
      queryClient.setQueryData(devboardKeys.task(moved.id), moved);
      track("card_moved");
      if (moved.column !== task.column && RUN_STARTING_TARGETS.includes(moved.column)) {
        track("agent_run_started");
      }
    },
  });
}

export function useCreateTask() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: TaskCreate) => unwrap(client.POST("/tasks", { body })),
    onSuccess: (created) => {
      queryClient.setQueryData<Task[]>(devboardKeys.tasks(), (old) =>
        old ? [...old.filter((t) => t.id !== created.id), created] : old,
      );
      void queryClient.invalidateQueries({ queryKey: devboardKeys.tasks() });
    },
  });
}

/** Content edits surface a 409 to the user (never auto-retried, or a concurrent edit is lost). */
export function useUpdateTask(taskId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: TaskUpdate) =>
      unwrap(client.PATCH("/tasks/{taskId}", { params: { path: { taskId } }, body })),
    onSuccess: (updated) => {
      queryClient.setQueryData(devboardKeys.task(taskId), updated);
      queryClient.setQueryData<Task[]>(devboardKeys.tasks(), (old) =>
        old?.map((t) => (t.id === updated.id ? updated : t)),
      );
      void queryClient.invalidateQueries({ queryKey: devboardKeys.triage(taskId) });
    },
    onError: (error) => {
      if (isApiError(error) && error.status === 409) {
        void queryClient.invalidateQueries({ queryKey: devboardKeys.task(taskId) });
      }
    },
  });
}

export function useDeleteTask(taskId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (version: number) =>
      unwrap(
        client.DELETE("/tasks/{taskId}", {
          params: { path: { taskId }, query: { version } },
        }),
      ),
    onSuccess: () => {
      queryClient.setQueryData<Task[]>(devboardKeys.tasks(), (old) =>
        old?.filter((t) => t.id !== taskId),
      );
      queryClient.removeQueries({ queryKey: devboardKeys.task(taskId) });
    },
    onError: (error) => {
      if (isApiError(error) && error.status === 409) {
        void queryClient.invalidateQueries({ queryKey: devboardKeys.task(taskId) });
      }
    },
  });
}

export function useApprovePlan(taskId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const { track } = useAnalytics();
  return useMutation({
    mutationFn: ({ plan, task }: { plan: Plan; task: Task }) =>
      unwrap(
        client.POST("/tasks/{taskId}/plan/approve", {
          params: { path: { taskId } },
          body: {
            plan_version: plan.version,
            task_version: task.version,
            approve_tests: true,
            approve_plan: true,
          },
        }),
      ),
    onSuccess: (updated) => {
      queryClient.setQueryData(devboardKeys.task(taskId), updated);
      void queryClient.invalidateQueries({ queryKey: devboardKeys.plan(taskId) });
      void queryClient.invalidateQueries({ queryKey: devboardKeys.tasks() });
      track("agent_run_approved");
    },
    onError: (error) => {
      if (isApiError(error) && error.status === 409) {
        void queryClient.invalidateQueries({ queryKey: devboardKeys.plan(taskId) });
        void queryClient.invalidateQueries({ queryKey: devboardKeys.task(taskId) });
      }
    },
  });
}

export function useKillTask(taskId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ version, reason }: { version: number; reason?: string }) =>
      unwrap(
        client.POST("/tasks/{taskId}/kill", {
          params: { path: { taskId } },
          body: { version, ...(reason ? { reason } : {}) },
        }),
      ),
    onSuccess: (updated) => {
      queryClient.setQueryData(devboardKeys.task(taskId), updated);
      void queryClient.invalidateQueries({ queryKey: devboardKeys.runs(taskId) });
      void queryClient.invalidateQueries({ queryKey: devboardKeys.tasks() });
    },
    onError: (error) => {
      if (isApiError(error) && error.status === 409) {
        void queryClient.invalidateQueries({ queryKey: devboardKeys.task(taskId) });
      }
    },
  });
}
