"use client";

import { useQuery } from "@tanstack/react-query";
import { type Schemas, unwrap, useApiClient } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export type UserSettings = Schemas["UserSettings"];

/** The one cache key for `GET /settings`, shared by the Settings page and its readers. */
export const settingsQueryKey = ["settings"] as const;

/** The caller's settings (profile, thresholds, defaults, display time zone). */
export function useUserSettings() {
  const client = useApiClient();
  const { status } = useAuth();
  return useQuery({
    queryKey: settingsQueryKey,
    queryFn: () => unwrap(client.GET("/settings")),
    enabled: status === "signed-in",
    staleTime: 60_000,
  });
}
