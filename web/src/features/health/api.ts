"use client";

import { useQuery } from "@tanstack/react-query";
import { unwrap, useApiClient } from "@/lib/api";

const MINUTE = 60_000;

/** Maintainer only (the API returns 403 otherwise), so callers pass `enabled`. */
export function useHealthSummary(enabled: boolean) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["health", "summary"],
    queryFn: () => unwrap(client.GET("/health/summary")),
    enabled,
    refetchInterval: enabled ? 5 * MINUTE : false,
  });
}

/** LLM spend for the last 30 days by week, for the feature and provider charts. */
export function useSpend(enabled: boolean) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["health", "llm-spend"],
    queryFn: () => {
      const from = new Date(Date.now() - 30 * 86_400_000).toISOString().slice(0, 10);
      return unwrap(
        client.GET("/dashboard/llm-spend", { params: { query: { from, bucket: "week" } } }),
      );
    },
    enabled,
  });
}
