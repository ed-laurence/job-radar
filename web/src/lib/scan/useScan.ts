"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { unwrap, useApiClient } from "@/lib/api";

/** The top bar and the dashboard both refresh every 30 s, as job-seeker does. */
export const SCAN_POLL_MS = 30_000;

export const scanStatusKey = ["scan", "status"] as const;

/** Maintainer only (the API answers 403 otherwise), so callers pass `enabled`. */
export function useScanStatus(enabled: boolean) {
  const client = useApiClient();
  return useQuery({
    queryKey: scanStatusKey,
    queryFn: () => unwrap(client.GET("/scan/status")),
    enabled,
    refetchInterval: enabled ? SCAN_POLL_MS : false,
  });
}

/** Runs a scan now; the answer is the finished run. Refreshes everything a scan can change. */
export function useRunScan() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(client.POST("/scan")),
    onSettled: () => {
      for (const key of [["scan"], ["dashboard"], ["roles"], ["health"]]) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    },
  });
}
