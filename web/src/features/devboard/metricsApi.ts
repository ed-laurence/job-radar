"use client";

import { useQuery } from "@tanstack/react-query";
import { unwrap, useApiClient } from "@/lib/api";

/** Flow and agent metrics for the board (API default window). */
export function useDevMetrics(enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["devboard", "metrics"],
    queryFn: () => unwrap(client.GET("/dashboard/dev-metrics")),
    enabled,
  });
}

/** Agent pass rate per week over the API's default 90 days. */
export function usePassRate(enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["devboard", "agent-pass-rate"],
    queryFn: () =>
      unwrap(client.GET("/dashboard/agent-pass-rate", { params: { query: { bucket: "week" } } })),
    enabled,
  });
}
