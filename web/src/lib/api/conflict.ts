import type { QueryClient, QueryKey } from "@tanstack/react-query";
import { currentVersionOf, isVersionConflict } from "./errors";

/**
 * On a 409 version conflict, refetch the stale data so the user sees the current state.
 * Returns true when the error was a conflict (callers then show a "changed elsewhere" notice).
 */
export async function handleVersionConflict(
  error: unknown,
  queryClient: QueryClient,
  queryKey: QueryKey,
): Promise<boolean> {
  if (!isVersionConflict(error)) return false;
  await queryClient.invalidateQueries({ queryKey });
  return true;
}

export interface RetryOptions {
  maxRetries?: number;
}

/**
 * Re-sends a write with the server's `current_version` after a conflict.
 * Only for last-write-wins operations such as rank moves; edits to content must surface the
 * conflict to the user instead, or a concurrent change is silently overwritten.
 */
export async function retryOnVersionConflict<T>(
  attempt: (version: number) => Promise<T>,
  version: number,
  { maxRetries = 1 }: RetryOptions = {},
): Promise<T> {
  let current = version;
  for (let tries = 0; ; tries++) {
    try {
      return await attempt(current);
    } catch (error) {
      const next = currentVersionOf(error);
      if (next === undefined || tries >= maxRetries) throw error;
      current = next;
    }
  }
}
