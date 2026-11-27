"use client";

import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { type Schemas, unwrap, useApiClient } from "@/lib/api";
import type { TargetLane, TargetStatus } from "./labels";

export type Target = Schemas["Target"];

export interface TargetFilters {
  status: TargetStatus | "";
  lane: TargetLane | "";
}

export const NO_FILTERS: TargetFilters = { status: "", lane: "" };

/** Everything under `["targets"]`, so a write also refreshes other views of the list (roles). */
export const targetKeys = {
  all: ["targets"] as const,
  pages: (filters: TargetFilters, q = "") => ["targets", "pages", filters, { q }] as const,
};

const PAGE_SIZE = 50;

/** Targets matching the filters; `q` (trimmed, blank for none) searches across every lane. */
export function useTargetList(filters: TargetFilters, q = "") {
  const client = useApiClient();
  return useInfiniteQuery({
    // A new search or filter is a new key, so paging starts again from the first page.
    queryKey: targetKeys.pages(filters, q),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      unwrap(
        client.GET("/targets", {
          params: {
            query: {
              limit: PAGE_SIZE,
              ...(pageParam ? { cursor: pageParam } : {}),
              ...(filters.status ? { status: filters.status } : {}),
              ...(filters.lane ? { lane: filters.lane } : {}),
              ...(q ? { q } : {}),
            },
          },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor,
  });
}

function useInvalidate() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: targetKeys.all });
}

export function useCreateTarget() {
  const client = useApiClient();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (body: Schemas["TargetCreate"]) => unwrap(client.POST("/targets", { body })),
    onSuccess: invalidate,
  });
}

export function useUpdateTarget() {
  const client = useApiClient();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Schemas["TargetUpdate"] }) =>
      unwrap(client.PATCH("/targets/{targetId}", { params: { path: { targetId: id } }, body })),
    onSuccess: invalidate,
  });
}

export function useDeleteTarget() {
  const client = useApiClient();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, version }: { id: string; version: number }) =>
      unwrap(
        client.DELETE("/targets/{targetId}", {
          params: { path: { targetId: id }, query: { version } },
        }),
      ),
    onSettled: invalidate,
  });
}

/** The latest saved target, for showing next to a draft after a version conflict. */
export function useFetchTarget() {
  const client = useApiClient();
  return (id: string) =>
    unwrap(client.GET("/targets/{targetId}", { params: { path: { targetId: id } } }));
}
