import { toApiError } from "./errors";
import { createApiClient } from "./generated/client";

export type ApiClient = ReturnType<typeof createApiClient>;
export type TokenSource = () => Promise<string | null>;

export interface ApiClientOptions {
  /** Defaults to `/api` on the web origin, which Next proxies to the api service. */
  baseUrl?: string;
  getToken: TokenSource;
  onUnauthenticated?: () => void;
  fetch?: typeof globalThis.fetch;
}

export const DEFAULT_API_BASE = "/api";

export function createJobRadarClient(options: ApiClientOptions): ApiClient {
  const client = createApiClient({
    baseUrl: options.baseUrl ?? DEFAULT_API_BASE,
    ...(options.fetch ? { fetch: options.fetch } : {}),
  });
  client.use({
    async onRequest({ request }) {
      const token = await options.getToken();
      if (token) request.headers.set("Authorization", `Bearer ${token}`);
      return request;
    },
    onResponse({ response }) {
      if (response.status === 401) options.onUnauthenticated?.();
      return response;
    },
  });
  return client;
}

interface FetchResult<D> {
  data?: D;
  error?: unknown;
  response: Response;
}

/** Resolves to the response data, or throws an ApiError carrying the problem details. */
export async function unwrap<D>(result: Promise<FetchResult<D>>): Promise<D> {
  const { data, error, response } = await result;
  if (!response.ok) {
    throw toApiError(response.status, error, response.headers.get("X-Correlation-Id"));
  }
  return data as D;
}
