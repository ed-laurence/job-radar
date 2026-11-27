import { screen } from "@testing-library/react";
import { http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { API, meHandler, renderWithApi, TESTER } from "@/lib/testing";
import { meQueryKey, useMe, useRole } from "./index";

let meCalls = 0;
const server = setupServer(meHandler);
server.events.on("request:start", ({ request }) => {
  if (new URL(request.url).pathname.endsWith("/me")) meCalls += 1;
});
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  meCalls = 0;
});
afterAll(() => server.close());

function MeEmail() {
  const me = useMe();
  return <p>email: {me.data?.email ?? "…"}</p>;
}

function Role() {
  const role = useRole();
  return <p>role: {role ?? "unknown"}</p>;
}

describe("lib/session", () => {
  it("serves useMe and useRole from one /me cache entry", async () => {
    const { queryClient } = renderWithApi(
      <>
        <MeEmail />
        <Role />
      </>,
    );
    expect(await screen.findByText("role: maintainer")).toBeInTheDocument();
    expect(screen.getByText("email: owner@example.test")).toBeInTheDocument();
    expect(meCalls).toBe(1);
    expect(queryClient.getQueryData(meQueryKey("owner@example.test"))).toMatchObject({
      role: "maintainer",
    });
  });

  it("keys the cache by account, so a different account gets its own role", async () => {
    renderWithApi(<Role />, { email: TESTER });
    expect(await screen.findByText("role: user")).toBeInTheDocument();
  });

  it("leaves the role unknown while /me fails", async () => {
    server.use(http.get(`${API}/me`, () => new Response(null, { status: 500 })));
    renderWithApi(<Role />);
    expect(screen.getByText("role: unknown")).toBeInTheDocument();
  });
});
