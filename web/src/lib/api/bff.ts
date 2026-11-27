/**
 * Backend-for-frontend proxy (server only). The browser calls same-origin `/api/*` and
 * `/hooks/github`; Next route handlers pass them to the private api service through here.
 * Never import this from client code.
 */

/** Request headers the browser may send to the API. Everything else (cookies, forwarding headers) is dropped. */
export const API_REQUEST_HEADERS = [
  "authorization",
  "if-match",
  "content-type",
  "accept",
  "last-event-id",
] as const;

/** GitHub webhooks: the backend verifies X-Hub-Signature-256 over the raw body. */
export const GITHUB_HOOK_REQUEST_HEADERS = [
  "content-type",
  "x-hub-signature-256",
  "x-github-event",
  "x-github-delivery",
  "x-github-hook-id",
] as const;

const RESPONSE_HEADERS = [
  "content-type",
  "cache-control",
  "content-disposition",
  "etag",
  "location",
  "retry-after",
  "x-correlation-id",
  "x-content-type-options",
  "ratelimit-limit",
  "ratelimit-remaining",
  "ratelimit-reset",
];

const HOP_BY_HOP_METHODS_WITHOUT_BODY = new Set(["GET", "HEAD"]);

export interface ProxyOptions {
  upstreamBase: string;
  allowHeaders?: readonly string[];
  /** Prod only: a Google ID token for the api audience, sent where Cloud Run IAM reads it. */
  getIdToken?: () => Promise<string>;
  fetch?: typeof globalThis.fetch;
}

function problem(status: number, title: string): Response {
  return new Response(
    JSON.stringify({
      type: "/problems/internal",
      title,
      status,
      correlation_id: crypto.randomUUID(),
    }),
    {
      status,
      headers: { "content-type": "application/problem+json", "cache-control": "no-store" },
    },
  );
}

export async function proxyToApi(request: Request, options: ProxyOptions): Promise<Response> {
  const incoming = new URL(request.url);
  const target = new URL(`${incoming.pathname}${incoming.search}`, options.upstreamBase);
  const allow = options.allowHeaders ?? API_REQUEST_HEADERS;

  const headers = new Headers();
  for (const name of allow) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }

  const doFetch = options.fetch ?? globalThis.fetch.bind(globalThis);
  let upstream: Response;
  try {
    if (options.getIdToken) {
      // Cloud Run checks X-Serverless-Authorization for IAM and leaves Authorization (the
      // user's Identity Platform token) for the app.
      headers.set("x-serverless-authorization", `Bearer ${await options.getIdToken()}`);
    }
    const raw = HOP_BY_HOP_METHODS_WITHOUT_BODY.has(request.method)
      ? undefined
      : await request.arrayBuffer();
    // Forward a body only when there is one: an empty ArrayBuffer on a bodiless DELETE makes
    // Node's fetch (undici) fail with "body length does not match content-length" (found in e2e).
    const body = raw !== undefined && raw.byteLength > 0 ? raw : undefined;
    upstream = await doFetch(target, {
      method: request.method,
      headers,
      cache: "no-store",
      redirect: "manual",
      signal: request.signal,
      ...(body !== undefined ? { body } : {}),
    });
  } catch {
    // The error text can name internal hosts or addresses, so none of it is returned.
    return problem(502, "Upstream unavailable");
  }

  const out = new Headers();
  for (const name of RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value !== null) out.set(name, value);
  }
  if (out.get("content-type")?.includes("text/event-stream")) {
    out.set("cache-control", "no-cache, no-transform");
    out.set("x-accel-buffering", "no");
  }
  // The body stream is handed straight back, so SSE chunks reach the browser as they arrive.
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: out,
  });
}

export interface IdTokenSourceOptions {
  audience: string;
  fetch?: typeof globalThis.fetch;
  now?: () => number;
}

const METADATA_IDENTITY_URL =
  "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity";
// Google ID tokens live for an hour; refresh well before that.
const TOKEN_TTL_MS = 45 * 60 * 1000;

/** Fetches (and caches) an ID token for `audience` from the Cloud Run metadata server. */
export function createIdTokenSource(options: IdTokenSourceOptions): () => Promise<string> {
  const doFetch = options.fetch ?? globalThis.fetch.bind(globalThis);
  const now = options.now ?? Date.now;
  let cached: { token: string; expires: number } | undefined;
  return async () => {
    if (cached && cached.expires > now()) return cached.token;
    const url = `${METADATA_IDENTITY_URL}?audience=${encodeURIComponent(options.audience)}&format=full`;
    const res = await doFetch(url, { headers: { "Metadata-Flavor": "Google" }, cache: "no-store" });
    if (!res.ok) throw new Error(`metadata identity token request failed (${res.status})`);
    const token = (await res.text()).trim();
    cached = { token, expires: now() + TOKEN_TTL_MS };
    return token;
  };
}

/** Proxy settings from server env. API_INTERNAL_URL (or dev.sh's API_BASE_URL) is the api service. */
export function proxyOptionsFromEnv(
  env: Record<string, string | undefined>,
  allowHeaders?: readonly string[],
): ProxyOptions {
  const upstreamBase = env.API_INTERNAL_URL ?? env.API_BASE_URL ?? "http://127.0.0.1:8000";
  const audience = env.API_ID_TOKEN_AUDIENCE;
  return {
    upstreamBase,
    ...(allowHeaders ? { allowHeaders } : {}),
    ...(audience ? { getIdToken: getSharedIdTokenSource(audience) } : {}),
  };
}

const sources = new Map<string, () => Promise<string>>();
function getSharedIdTokenSource(audience: string): () => Promise<string> {
  let source = sources.get(audience);
  if (!source) {
    source = createIdTokenSource({ audience });
    sources.set(audience, source);
  }
  return source;
}
