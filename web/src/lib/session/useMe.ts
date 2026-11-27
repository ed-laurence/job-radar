"use client";

import { useQuery } from "@tanstack/react-query";
import { type Schemas, unwrap, useApiClient } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export type Me = Schemas["Me"];
export type AppRole = Schemas["AppRole"];

/** The one cache key for `GET /me`. Keyed by email so switching accounts refetches. */
export function meQueryKey(email: string | null) {
  return ["me", email] as const;
}

/** The signed-in account and its role, as the API sees it. */
export function useMe() {
  const client = useApiClient();
  const { user, status } = useAuth();
  return useQuery({
    queryKey: meQueryKey(user?.email ?? null),
    queryFn: () => unwrap(client.GET("/me")),
    enabled: status === "signed-in",
    staleTime: 5 * 60_000,
  });
}

/** The caller's role, or undefined while unknown. UX only; the API enforces roles (SEC-02). */
export function useRole(): AppRole | undefined {
  return useMe().data?.role;
}
