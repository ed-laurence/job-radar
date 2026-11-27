"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isApiError, type Schemas, unwrap, useApiClient } from "@/lib/api";
import { devboardKeys } from "./api";
import type { Task } from "./model";

export type TaskChatMessage = Schemas["TaskChatMessage"];
export type TaskChatProposal = Schemas["TaskChatProposal"];
export type TaskChatProposalAccepted = Schemas["TaskChatProposalAccepted"];

/** `TaskChatMessageCreate.content` maxLength in the spec. */
export const TASK_CHAT_MAX_CHARS = 4000;
const PAGE_SIZE = 100;
const MAX_PAGES = 10;

/** This account's conversation about one card, oldest first (every page, up to a cap). */
export function useTaskChat(taskId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: devboardKeys.chat(taskId),
    queryFn: async () => {
      const items: TaskChatMessage[] = [];
      let cursor: string | undefined;
      for (let page = 0; page < MAX_PAGES; page++) {
        const data = await unwrap(
          client.GET("/tasks/{taskId}/chat", {
            params: {
              path: { taskId },
              query: { limit: PAGE_SIZE, ...(cursor ? { cursor } : {}) },
            },
          }),
        );
        items.push(...data.items);
        cursor = data.next_cursor;
        if (!cursor) break;
      }
      return items;
    },
  });
}

export function useClearTaskChat(taskId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(client.DELETE("/tasks/{taskId}/chat", { params: { path: { taskId } } })),
    onSuccess: () => {
      queryClient.setQueryData<TaskChatMessage[]>(devboardKeys.chat(taskId), []);
      void queryClient.invalidateQueries({ queryKey: devboardKeys.chat(taskId) });
    },
  });
}

function replaceProposal(
  messages: TaskChatMessage[] | undefined,
  proposal: TaskChatProposal,
): TaskChatMessage[] | undefined {
  return messages?.map((m) => (m.proposal?.id === proposal.id ? { ...m, proposal } : m));
}

/**
 * Accepting edits the card, so the card, the board and its triage and plan are refreshed. A 409
 * (stale version, a changed field, a run active) reloads the card and the chat so a retry sends
 * the current version; it is never retried automatically.
 */
export function useAcceptTaskChatProposal(taskId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ proposalId, version }: { proposalId: string; version: number }) =>
      unwrap(
        client.POST("/tasks/{taskId}/chat/proposals/{proposalId}/accept", {
          params: { path: { taskId, proposalId } },
          body: { version },
        }),
      ),
    onSuccess: (result) => {
      queryClient.setQueryData(devboardKeys.task(taskId), result.task);
      queryClient.setQueryData<Task[]>(devboardKeys.tasks(), (old) =>
        old?.map((t) => (t.id === result.task.id ? result.task : t)),
      );
      queryClient.setQueryData<TaskChatMessage[]>(devboardKeys.chat(taskId), (old) =>
        replaceProposal(old, result.proposal),
      );
      void queryClient.invalidateQueries({ queryKey: devboardKeys.task(taskId) });
      void queryClient.invalidateQueries({ queryKey: devboardKeys.tasks() });
      void queryClient.invalidateQueries({ queryKey: devboardKeys.chat(taskId) });
    },
    onError: async (error) => {
      if (isApiError(error) && error.status === 409) {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: devboardKeys.task(taskId) }),
          queryClient.invalidateQueries({ queryKey: devboardKeys.chat(taskId) }),
        ]);
      }
    },
  });
}

export function useRejectTaskChatProposal(taskId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (proposalId: string) =>
      unwrap(
        client.POST("/tasks/{taskId}/chat/proposals/{proposalId}/reject", {
          params: { path: { taskId, proposalId } },
        }),
      ),
    onSuccess: (proposal) => {
      queryClient.setQueryData<TaskChatMessage[]>(devboardKeys.chat(taskId), (old) =>
        replaceProposal(old, proposal),
      );
      void queryClient.invalidateQueries({ queryKey: devboardKeys.chat(taskId) });
    },
    onError: (error) => {
      if (isApiError(error) && error.status === 409) {
        void queryClient.invalidateQueries({ queryKey: devboardKeys.chat(taskId) });
      }
    },
  });
}
