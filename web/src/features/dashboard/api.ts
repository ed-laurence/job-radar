"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type Schemas, unwrap, useApiClient } from "@/lib/api";
import { SCAN_POLL_MS } from "@/lib/scan";
import { TABS, type Tab } from "./tabs";

export type Bucket = "day" | "week" | "month";

export interface Range {
  /** Inclusive start (UTC date). Absent: the API default of 90 days ago. */
  from?: string;
  bucket: Bucket;
}

function query(range: Range) {
  return {
    params: { query: { bucket: range.bucket, ...(range.from ? { from: range.from } : {}) } },
  };
}

export const dashboardKeys = {
  summary: ["dashboard", "job-summary"] as const,
  cards: ["dashboard", "cards"] as const,
  tab: (tab: Tab, track?: Track) => ["dashboard", "cards", tab, track ?? "all"] as const,
  contacts: ["dashboard", "outreach"] as const,
  rejections: ["dashboard", "rejection-patterns"] as const,
  alerts: ["dashboard", "alerts"] as const,
  campaign: ["campaign"] as const,
  plan: (week?: number) => ["campaign", "plan", week ?? "current"] as const,
  tasks: (week?: number) => ["campaign", "tasks", week ?? "current"] as const,
  activity: (week?: number) => ["campaign", "activity", week ?? "current"] as const,
};

export type Track = Schemas["Track"];

/** As job-seeker: refresh every 30 s so roles from scheduled scans appear without a reload. */
export const DASHBOARD_POLL_MS = SCAN_POLL_MS;

export function useJobSummary() {
  const client = useApiClient();
  return useQuery({
    queryKey: dashboardKeys.summary,
    queryFn: () => unwrap(client.GET("/dashboard/job-summary")),
    refetchInterval: DASHBOARD_POLL_MS,
  });
}

export const PAGE_SIZE = 20;

/** Roles for one status tab, a page at a time (cursor paging). */
export function useTabRoles(tab: Tab, track?: Track) {
  const client = useApiClient();
  const t = TABS[tab];
  return useInfiniteQuery({
    queryKey: dashboardKeys.tab(tab, track),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      unwrap(
        client.GET("/roles", {
          params: {
            query: {
              limit: PAGE_SIZE,
              sort: t.sort,
              ...(t.status.length ? { status: [...t.status] } : {}),
              ...(t.stage.length ? { stage: [...t.stage] } : {}),
              ...(track ? { track } : {}),
              ...(pageParam ? { cursor: pageParam } : {}),
            },
          },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor,
    refetchInterval: DASHBOARD_POLL_MS,
  });
}

/** People to message, for the Outreach tab (the full list lives on the Contacts page). */
export function useOutreach() {
  const client = useApiClient();
  return useQuery({
    queryKey: dashboardKeys.contacts,
    queryFn: () => unwrap(client.GET("/contacts", { params: { query: { limit: 50 } } })),
  });
}

/** Rejection counts by category and recent lessons, for the Feedback tab. */
export function useRejectionPatterns() {
  const client = useApiClient();
  return useQuery({
    queryKey: dashboardKeys.rejections,
    queryFn: () => unwrap(client.GET("/rejections/patterns")),
  });
}

export interface StatusChange {
  role: Schemas["Role"];
  status: Schemas["RoleStatus"];
}

/** Moves a role to another status with the version it was read at (409 on a stale version). */
export function useSetStatus() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: dashboardKeys.cards });
    void queryClient.invalidateQueries({ queryKey: dashboardKeys.summary });
    void queryClient.invalidateQueries({ queryKey: ["roles"] });
  };
  return useMutation({
    mutationFn: ({ role, status }: StatusChange) =>
      unwrap(
        client.POST("/roles/{roleId}/status", {
          params: { path: { roleId: role.id } },
          body: { status, version: role.version },
        }),
      ),
    onSettled: refresh,
  });
}

export function useFunnel(range: Range) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["dashboard", "funnel", range],
    queryFn: () => unwrap(client.GET("/dashboard/funnel", query(range))),
  });
}

export function usePostToApply(range: Range) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["dashboard", "post-to-apply", range],
    queryFn: () => unwrap(client.GET("/dashboard/post-to-apply", query(range))),
  });
}

export function useScores(range: Range) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["dashboard", "score-distribution", range],
    queryFn: () => unwrap(client.GET("/dashboard/score-distribution", query(range))),
  });
}

export function useRejections(range: Range) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["dashboard", "rejection-reasons", range],
    queryFn: () => unwrap(client.GET("/dashboard/rejection-reasons", query(range))),
  });
}

/** Extra job-search metrics for the insights disclosure (date range only, no bucket). */
export function useJobInsights(range: Range) {
  const client = useApiClient();
  const from = range.from;
  return useQuery({
    queryKey: ["dashboard", "job-insights", from ?? "default"],
    queryFn: () =>
      unwrap(
        client.GET("/dashboard/job-insights", {
          params: { query: from ? { from } : {} },
        }),
      ),
  });
}

/** Main-page alerts (failed jobs, follow-ups due, quiet feeds). */
export function useAlerts() {
  const client = useApiClient();
  return useQuery({
    queryKey: dashboardKeys.alerts,
    queryFn: () => unwrap(client.GET("/alerts")),
    refetchInterval: DASHBOARD_POLL_MS,
  });
}

/** Moves Act now roles posted more than `days` days ago to Passed (closed). */
export function useArchiveOlder() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (days: number) => unwrap(client.POST("/roles/archive-older", { body: { days } })),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: dashboardKeys.cards });
      void queryClient.invalidateQueries({ queryKey: dashboardKeys.summary });
      void queryClient.invalidateQueries({ queryKey: ["roles"] });
    },
  });
}

const weekQuery = (week?: number) => ({ params: { query: week ? { week } : {} } });

/** The plan panel for a week (default: the current week). */
export function useCampaignPlan(week?: number) {
  const client = useApiClient();
  return useQuery({
    queryKey: dashboardKeys.plan(week),
    queryFn: () => unwrap(client.GET("/campaign", weekQuery(week))),
  });
}

export function useCampaignTasks(week?: number) {
  const client = useApiClient();
  return useQuery({
    queryKey: dashboardKeys.tasks(week),
    queryFn: () => unwrap(client.GET("/campaign/tasks", weekQuery(week))),
  });
}

export function useCampaignActivity(week?: number) {
  const client = useApiClient();
  return useQuery({
    queryKey: dashboardKeys.activity(week),
    queryFn: () => unwrap(client.GET("/campaign/activity", weekQuery(week))),
  });
}

export interface TaskDone {
  taskId: string;
  week: number;
  done: boolean;
}

/** Ticks or clears a task for a week; refreshes the plan, since some tasks track a metric. */
export function useToggleTask() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ taskId, week, done }: TaskDone) => {
      const init = { params: { path: { taskId }, query: { week } } };
      if (done) await unwrap(client.PUT("/campaign/tasks/{taskId}/done", init));
      else await unwrap(client.DELETE("/campaign/tasks/{taskId}/done", init));
    },
    onSettled: () => void queryClient.invalidateQueries({ queryKey: dashboardKeys.campaign }),
  });
}

export function useLogActivity() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["CampaignActivityCreate"]) =>
      unwrap(client.POST("/campaign/activity", { body })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: dashboardKeys.campaign }),
  });
}
