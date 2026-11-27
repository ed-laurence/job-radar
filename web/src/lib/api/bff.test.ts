import { describe, expect, it, vi } from "vitest";
import {
  API_REQUEST_HEADERS,
  createIdTokenSource,
  GITHUB_HOOK_REQUEST_HEADERS,
  proxyToApi,
} from "./bff";

const UPSTREAM = "http://api.internal.test";

function incoming(path: string, init: RequestInit = {}) {
  return new Request(`http://web.test${path}`, init);
}

function okUpstream(body: BodyInit | null = "{}", headers: Record<string, string> = {}) {
  return vi.fn<typeof fetch>().mockResolvedValue(
    new Response(body, {
      status: 200,
      headers: { "content-type": "application/json", ...headers },
    }),
  );
}

describe("proxyToApi: request", () => {
  it("forwards path and query to the upstream base", async () => {
    const fetchMock = okUpstream();
    await proxyToApi(incoming("/api/roles?limit=5&cursor=abc"), {
      upstreamBase: UPSTREAM,
      fetch: fetchMock,
    });
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(`${UPSTREAM}/api/roles?limit=5&cursor=abc`);
    expect(fetchMock.mock.calls[0]?.[1]?.cache).toBe("no-store");
  });

  // Regression (e2e): a bodiless DELETE was forwarded with an empty ArrayBuffer, which made
  // Node's fetch fail ("body length does not match content-length") and every delete 502.
  it("forwards no body for a bodiless DELETE", async () => {
    const fetchMock = okUpstream(null);
    await proxyToApi(incoming("/api/contacts/c1?version=2", { method: "DELETE" }), {
      upstreamBase: UPSTREAM,
      fetch: fetchMock,
    });
    const init = fetchMock.mock.calls[0]?.[1];
    expect(init?.method).toBe("DELETE");
    expect(init && "body" in init).toBe(false);
  });

  it("forwards only allowlisted request headers", async () => {
    const fetchMock = okUpstream();
    await proxyToApi(
      incoming("/api/me", {
        headers: {
          Authorization: "Bearer fake:owner@example.test",
          "If-Match": '"3"',
          "Content-Type": "application/json",
          Accept: "application/json",
          "Last-Event-ID": "7",
          Cookie: "session=should-not-pass",
          "X-Forwarded-For": "203.0.113.9",
          "X-Serverless-Authorization": "Bearer spoofed",
          "X-Hub-Signature-256": "sha256=spoofed",
        },
      }),
      { upstreamBase: UPSTREAM, fetch: fetchMock },
    );
    const sent = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect([...sent.keys()].sort()).toEqual(
      ["accept", "authorization", "content-type", "if-match", "last-event-id"].sort(),
    );
    expect(sent.get("authorization")).toBe("Bearer fake:owner@example.test");
    expect(API_REQUEST_HEADERS).toHaveLength(5);
  });

  it("adds the Google ID token without replacing the user's token", async () => {
    const fetchMock = okUpstream();
    await proxyToApi(incoming("/api/me", { headers: { Authorization: "Bearer user-jwt" } }), {
      upstreamBase: UPSTREAM,
      fetch: fetchMock,
      getIdToken: async () => "google-id-token",
    });
    const sent = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(sent.get("x-serverless-authorization")).toBe("Bearer google-id-token");
    expect(sent.get("authorization")).toBe("Bearer user-jwt");
  });

  it("preserves the raw body byte for byte (webhook HMAC)", async () => {
    const fetchMock = okUpstream();
    const raw = '{"b":1,  "a":"é"}\n';
    await proxyToApi(
      incoming("/hooks/github", {
        method: "POST",
        body: raw,
        headers: {
          "Content-Type": "application/json",
          "X-Hub-Signature-256": "sha256=abc",
          "X-GitHub-Event": "pull_request",
          "X-GitHub-Delivery": "00000000-0000-4000-8000-000000000001",
        },
      }),
      { upstreamBase: UPSTREAM, fetch: fetchMock, allowHeaders: GITHUB_HOOK_REQUEST_HEADERS },
    );
    const init = fetchMock.mock.calls[0]?.[1];
    const body = Array.from(new Uint8Array(init?.body as ArrayBuffer));
    expect(body).toEqual(Array.from(new TextEncoder().encode(raw)));
    expect(new Headers(init?.headers).get("x-hub-signature-256")).toBe("sha256=abc");
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(`${UPSTREAM}/hooks/github`);
  });

  it("returns a problem+json 502 when the upstream is unreachable", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockRejectedValue(new TypeError("ECONNREFUSED 10.0.0.1"));
    const res = await proxyToApi(incoming("/api/me"), { upstreamBase: UPSTREAM, fetch: fetchMock });
    expect(res.status).toBe(502);
    expect(res.headers.get("content-type")).toBe("application/problem+json");
    const text = await res.text();
    expect(text).not.toContain("10.0.0.1");
    expect(JSON.parse(text)).toMatchObject({ status: 502, type: "/problems/internal" });
  });
});

describe("proxyToApi: response", () => {
  it("passes only safe response headers through", async () => {
    const fetchMock = okUpstream("{}", {
      "x-correlation-id": "corr-1",
      "ratelimit-remaining": "9",
      "set-cookie": "a=b",
      server: "uvicorn",
    });
    const res = await proxyToApi(incoming("/api/me"), { upstreamBase: UPSTREAM, fetch: fetchMock });
    expect(res.headers.get("x-correlation-id")).toBe("corr-1");
    expect(res.headers.get("ratelimit-remaining")).toBe("9");
    expect(res.headers.get("set-cookie")).toBeNull();
    expect(res.headers.get("server")).toBeNull();
  });

  it("passes file-download headers through (kit PDFs)", async () => {
    const fetchMock = okUpstream("%PDF-1.7", {
      "content-type": "application/pdf",
      "content-disposition": 'attachment; filename="Alex_Example_CV.pdf"',
      "x-content-type-options": "nosniff",
    });
    const res = await proxyToApi(incoming("/api/roles/r1/kit/cv.pdf"), {
      upstreamBase: UPSTREAM,
      fetch: fetchMock,
    });
    expect(res.headers.get("content-disposition")).toBe(
      'attachment; filename="Alex_Example_CV.pdf"',
    );
    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
    expect(await res.text()).toBe("%PDF-1.7");
  });

  it("streams SSE through without buffering", async () => {
    const encoder = new TextEncoder();
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => {
      release = r;
    });
    const upstreamBody = new ReadableStream<Uint8Array>({
      async start(controller) {
        controller.enqueue(encoder.encode('event: step\nid: 0\ndata: {"seq":0}\n\n'));
        await gate; // the upstream is still open while the client reads the first event
        controller.enqueue(encoder.encode("event: done\nid: d\ndata: {}\n\n"));
        controller.close();
      },
    });
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(upstreamBody, {
        status: 200,
        headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
      }),
    );
    const res = await proxyToApi(
      incoming("/api/runs/x/steps", { headers: { Accept: "text/event-stream" } }),
      { upstreamBase: UPSTREAM, fetch: fetchMock },
    );
    expect(res.headers.get("content-type")).toBe("text/event-stream");
    expect(res.headers.get("x-accel-buffering")).toBe("no");
    const reader = res.body?.getReader();
    const first = await reader?.read();
    expect(new TextDecoder().decode(first?.value)).toContain("event: step");
    release();
    const second = await reader?.read();
    expect(new TextDecoder().decode(second?.value)).toContain("event: done");
  });

  it("passes the client's abort signal upstream", async () => {
    const fetchMock = okUpstream();
    const controller = new AbortController();
    await proxyToApi(incoming("/api/me", { signal: controller.signal }), {
      upstreamBase: UPSTREAM,
      fetch: fetchMock,
    });
    const signal = fetchMock.mock.calls[0]?.[1]?.signal;
    controller.abort();
    expect(signal?.aborted).toBe(true);
  });
});

describe("createIdTokenSource", () => {
  it("asks the metadata server for an audience-bound token and caches it", async () => {
    let now = 0;
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => new Response("tok-1", { status: 200 }));
    const source = createIdTokenSource({
      audience: "https://api.example.test",
      fetch: fetchMock,
      now: () => now,
    });
    expect(await source()).toBe("tok-1");
    expect(await source()).toBe("tok-1");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toContain("/instance/service-accounts/default/identity?audience=");
    expect(String(url)).toContain(encodeURIComponent("https://api.example.test"));
    expect(new Headers(init?.headers).get("metadata-flavor")).toBe("Google");
    now = 60 * 60 * 1000;
    await source();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("fails closed when the metadata server refuses", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response("no", { status: 404 }));
    const source = createIdTokenSource({ audience: "https://api.example.test", fetch: fetchMock });
    await expect(source()).rejects.toThrow();
  });
});
