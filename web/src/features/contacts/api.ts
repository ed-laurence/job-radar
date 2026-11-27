"use client";

import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { type Schemas, unwrap, useApiClient } from "@/lib/api";

export type Contact = Schemas["Contact"];
export const CONTACTS_KEY = ["contacts", "list"] as const;
const PAGE_SIZE = 50;

/** Contacts, newest first; `q` (trimmed, blank for none) searches names, company and email. */
export function useContacts(q = "") {
  const client = useApiClient();
  return useInfiniteQuery({
    // A new search is a new key, so paging starts again from the first page.
    queryKey: [...CONTACTS_KEY, { q }],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      unwrap(
        client.GET("/contacts", {
          params: {
            query: {
              limit: PAGE_SIZE,
              ...(pageParam ? { cursor: pageParam } : {}),
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
  return () => queryClient.invalidateQueries({ queryKey: CONTACTS_KEY });
}

export function useCreateContact() {
  const client = useApiClient();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (body: Schemas["ContactCreate"]) => unwrap(client.POST("/contacts", { body })),
    onSuccess: invalidate,
  });
}

export function useUpdateContact() {
  const client = useApiClient();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Schemas["ContactUpdate"] }) =>
      unwrap(client.PATCH("/contacts/{contactId}", { params: { path: { contactId: id } }, body })),
    onSuccess: invalidate,
  });
}

export function useDeleteContact() {
  const client = useApiClient();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, version }: { id: string; version: number }) =>
      unwrap(
        client.DELETE("/contacts/{contactId}", {
          params: { path: { contactId: id }, query: { version } },
        }),
      ),
    onSuccess: invalidate,
  });
}

/** The latest saved contact, for showing next to a draft after a version conflict. */
export function useFetchContact() {
  const client = useApiClient();
  return (id: string) =>
    unwrap(client.GET("/contacts/{contactId}", { params: { path: { contactId: id } } }));
}
