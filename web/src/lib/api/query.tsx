"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createContext, type ReactNode, useContext, useState } from "react";
import type { ApiClient } from "./client";
import { isApiError } from "./errors";

const MAX_RETRIES = 2;

/** Client errors (4xx) are final; only network and 5xx failures are retried. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && error.status < 500) return false;
  return failureCount < MAX_RETRIES;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: shouldRetry, staleTime: 30_000, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
}

const ApiClientContext = createContext<ApiClient | null>(null);

export interface ApiProviderProps {
  client: ApiClient;
  queryClient?: QueryClient;
  children: ReactNode;
}

export function ApiProvider({ client, queryClient, children }: ApiProviderProps) {
  const [fallbackClient] = useState(createQueryClient);
  return (
    <ApiClientContext.Provider value={client}>
      <QueryClientProvider client={queryClient ?? fallbackClient}>{children}</QueryClientProvider>
    </ApiClientContext.Provider>
  );
}

export function useApiClient(): ApiClient {
  const client = useContext(ApiClientContext);
  if (!client) throw new Error("useApiClient must be used inside <ApiProvider>");
  return client;
}
