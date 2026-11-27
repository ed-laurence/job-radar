import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { createJobRadarClient, unwrap } from "./client";
import { ApiError } from "./errors";

const BASE = "http://api.test/api";
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const me = {
  id: "00000000-0000-4000-8000-000000000001",
  email: "owner@example.test",
  role: "maintainer",
};

function problem(status: number, type: string) {
  return HttpResponse.json(
    { type, title: "Problem", status, correlation_id: `corr-${status}` },
    { status, headers: { "content-type": "application/problem+json" } },
  );
}

describe("createJobRadarClient", () => {
  it("injects the bearer token on every request", async () => {
    let seen: string | null = null;
    server.use(
      http.get(`${BASE}/me`, ({ request }) => {
        seen = request.headers.get("authorization");
        return HttpResponse.json(me);
      }),
    );
    const client = createJobRadarClient({
      baseUrl: BASE,
      getToken: async () => "fake:owner@example.test",
    });
    const data = await unwrap(client.GET("/me"));
    expect(seen).toBe("Bearer fake:owner@example.test");
    expect(data.email).toBe("owner@example.test");
  });

  it("sends no Authorization header when signed out", async () => {
    let had = true;
    server.use(
      http.get(`${BASE}/me`, ({ request }) => {
        had = request.headers.has("authorization");
        return HttpResponse.json(me);
      }),
    );
    const client = createJobRadarClient({ baseUrl: BASE, getToken: async () => null });
    await unwrap(client.GET("/me"));
    expect(had).toBe(false);
  });

  it("throws a typed ApiError for problem+json responses", async () => {
    server.use(http.get(`${BASE}/me`, () => problem(403, "/problems/forbidden")));
    const client = createJobRadarClient({ baseUrl: BASE, getToken: async () => "t" });
    const err = await unwrap(client.GET("/me")).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(403);
    expect((err as ApiError).correlationId).toBe("corr-403");
  });

  it("calls onUnauthenticated on a 401", async () => {
    server.use(http.get(`${BASE}/me`, () => problem(401, "/problems/unauthenticated")));
    const onUnauthenticated = vi.fn();
    const client = createJobRadarClient({
      baseUrl: BASE,
      getToken: async () => "t",
      onUnauthenticated,
    });
    await expect(unwrap(client.GET("/me"))).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthenticated).toHaveBeenCalledTimes(1);
  });

  it("returns undefined data for a body-less success", async () => {
    server.use(http.delete(`${BASE}/roles/:roleId`, () => new HttpResponse(null, { status: 204 })));
    const client = createJobRadarClient({ baseUrl: BASE, getToken: async () => "t" });
    const result = await unwrap(
      client.DELETE("/roles/{roleId}", {
        params: { path: { roleId: "00000000-0000-4000-8000-0000000000a1" }, query: { version: 1 } },
      }),
    );
    expect(result).toBeUndefined();
  });
});
