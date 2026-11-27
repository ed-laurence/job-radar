"use client";

import {
  useInfiniteQuery,
  useIsMutating,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { type Schemas, unwrap, useApiClient } from "@/lib/api";
import { type AgeRange, ageQuery } from "./age";
import type { Role, RoleSort, RoleStatus, Track } from "./labels";

export interface RoleFilters {
  q: string;
  status: RoleStatus[];
  track: Track | "";
  minScore: number | "";
  /** Days since posted (or found); kept in the URL. */
  age: AgeRange;
  sort: RoleSort;
}

export const EMPTY_FILTERS: RoleFilters = {
  q: "",
  status: [],
  track: "",
  minScore: "",
  age: { min: "", max: "" },
  sort: "rank",
};

export const PAGE_SIZE = 25;

export const roleKeys = {
  all: ["roles"] as const,
  list: (filters: RoleFilters) => ["roles", "list", filters] as const,
  detail: (id: string) => ["roles", "detail", id] as const,
  chat: (id: string) => ["roles", "chat", id] as const,
  write: (id: string) => ["roles", "write", id] as const,
};

export function useRoleList(filters: RoleFilters) {
  const client = useApiClient();
  return useInfiniteQuery({
    queryKey: roleKeys.list(filters),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      unwrap(
        client.GET("/roles", {
          params: {
            query: {
              limit: PAGE_SIZE,
              sort: filters.sort,
              ...(pageParam ? { cursor: pageParam } : {}),
              ...(filters.q.trim() ? { q: filters.q.trim() } : {}),
              ...(filters.status.length ? { status: filters.status } : {}),
              ...(filters.track ? { track: filters.track } : {}),
              ...(filters.minScore !== "" ? { min_score: filters.minScore } : {}),
              ...ageQuery(filters.age),
            },
          },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor,
  });
}

export function useRole(roleId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: roleKeys.detail(roleId),
    queryFn: () => unwrap(client.GET("/roles/{roleId}", { params: { path: { roleId } } })),
  });
}

export function useCreateRole() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["RoleCreate"]) => unwrap(client.POST("/roles", { body })),
    onSuccess: (role) => {
      queryClient.setQueryData(roleKeys.detail(role.id), role);
      void queryClient.invalidateQueries({ queryKey: [...roleKeys.all, "list"] });
    },
  });
}

/** Writes that return the updated role share this cache update. */
function useRoleWrite<V>(roleId: string, write: (vars: V) => Promise<Role>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: roleKeys.write(roleId),
    mutationFn: write,
    onSuccess: (role) => {
      queryClient.setQueryData(roleKeys.detail(role.id), role);
      void queryClient.invalidateQueries({ queryKey: [...roleKeys.all, "list"] });
    },
  });
}

export function useUpdateRole(roleId: string) {
  const client = useApiClient();
  return useRoleWrite(roleId, (body: Schemas["RoleUpdate"]) =>
    unwrap(client.PATCH("/roles/{roleId}", { params: { path: { roleId } }, body })),
  );
}

export function useChangeStatus(roleId: string) {
  const client = useApiClient();
  return useRoleWrite(roleId, (body: Schemas["RoleStatusChange"]) =>
    unwrap(client.POST("/roles/{roleId}/status", { params: { path: { roleId } }, body })),
  );
}

/**
 * True while any edit or status change for this role is in flight. A second write sent now would
 * carry the version from before the first one and fail with a conflict the user didn't cause.
 */
export function useRoleWritePending(roleId: string): boolean {
  return useIsMutating({ mutationKey: roleKeys.write(roleId) }) > 0;
}

export function useDeleteRole(roleId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (version: number) =>
      unwrap(
        client.DELETE("/roles/{roleId}", { params: { path: { roleId }, query: { version } } }),
      ),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: roleKeys.detail(roleId) });
      void queryClient.invalidateQueries({ queryKey: [...roleKeys.all, "list"] });
    },
  });
}

export function useChatMessages(roleId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: roleKeys.chat(roleId),
    queryFn: () =>
      unwrap(
        client.GET("/roles/{roleId}/chat/messages", {
          params: { path: { roleId }, query: { limit: 100 } },
        }),
      ),
  });
}

export function useClearChat(roleId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(client.DELETE("/roles/{roleId}/chat/messages", { params: { path: { roleId } } })),
    onSuccess: () => queryClient.setQueryData(roleKeys.chat(roleId), { items: [] }),
  });
}

export function useTargets() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["targets", "list"],
    queryFn: () => unwrap(client.GET("/targets", { params: { query: { limit: 100 } } })),
  });
}
