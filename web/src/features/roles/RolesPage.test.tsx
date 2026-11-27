import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { API, problem, renderWithApi } from "@/lib/testing";
import { NewRolePage } from "./NewRolePage";
import { RolesPage } from "./RolesPage";
import { makeRole } from "./testing";

const router = { push: vi.fn(), replace: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router, usePathname: () => "/roles" }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  router.push.mockReset();
});
afterAll(() => server.close());

const page1 = [
  makeRole({ id: "00000000-0000-4000-8000-000000000001", title: "Data Engineer" }),
  makeRole({ id: "00000000-0000-4000-8000-000000000002", title: "Analytics Lead", score: 64 }),
];
const page2 = [makeRole({ id: "00000000-0000-4000-8000-000000000003", title: "ML Engineer" })];

describe("RolesPage", () => {
  it("shows a loading state, then the first page, and loads more with the cursor", async () => {
    const cursors: (string | null)[] = [];
    server.use(
      http.get(`${API}/roles`, ({ request }) => {
        const cursor = new URL(request.url).searchParams.get("cursor");
        cursors.push(cursor);
        return HttpResponse.json(
          cursor === "c2" ? { items: page2 } : { items: page1, next_cursor: "c2" },
        );
      }),
    );
    renderWithApi(<RolesPage />);
    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);

    const table = await screen.findByRole("table", { name: "Roles" });
    expect(within(table).getByRole("link", { name: "Data Engineer" })).toHaveAttribute(
      "href",
      "/roles/00000000-0000-4000-8000-000000000001",
    );
    await userEvent.click(screen.getByRole("button", { name: "Load more roles" }));
    expect(await within(table).findByRole("link", { name: "ML Engineer" })).toBeInTheDocument();
    expect(cursors).toEqual([null, "c2"]);
    expect(screen.queryByRole("button", { name: "Load more roles" })).not.toBeInTheDocument();
  });

  it("sends filters as query parameters", async () => {
    const seen: URLSearchParams[] = [];
    server.use(
      http.get(`${API}/roles`, ({ request }) => {
        seen.push(new URL(request.url).searchParams);
        return HttpResponse.json({ items: page1 });
      }),
    );
    renderWithApi(<RolesPage />);
    await screen.findByRole("table", { name: "Roles" });

    await userEvent.type(
      screen.getByRole("searchbox", { name: "Search title or company" }),
      "acme",
    );
    await userEvent.click(screen.getByRole("checkbox", { name: "Applied" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Track" }), "exec");
    await userEvent.type(screen.getByRole("spinbutton", { name: "Minimum score" }), "70");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Sort" }), "-score");

    await waitFor(() => {
      const last = seen.at(-1);
      expect(last?.get("q")).toBe("acme");
      expect(last?.getAll("status")).toEqual(["applied"]);
      expect(last?.get("track")).toBe("exec");
      expect(last?.get("min_score")).toBe("70");
      expect(last?.get("sort")).toBe("-score");
    });
  });

  it("filters by age, keeps it in the URL and restarts paging", async () => {
    window.history.replaceState(null, "", "/roles");
    const seen: URLSearchParams[] = [];
    server.use(
      http.get(`${API}/roles`, ({ request }) => {
        const params = new URL(request.url).searchParams;
        seen.push(params);
        return HttpResponse.json(
          params.get("cursor") ? { items: page2 } : { items: page1, next_cursor: "c2" },
        );
      }),
    );
    renderWithApi(<RolesPage />);
    await screen.findByRole("table", { name: "Roles" });
    await userEvent.click(screen.getByRole("button", { name: "Load more roles" }));
    await waitFor(() => expect(seen.at(-1)?.get("cursor")).toBe("c2"));

    const age = screen.getByRole("combobox", { name: "Age" });
    await userEvent.selectOptions(age, "Last 7 days");
    await waitFor(() => expect(seen.at(-1)?.get("max_age_days")).toBe("7"));
    expect(seen.at(-1)?.get("cursor")).toBeNull();
    expect(seen.at(-1)?.has("min_age_days")).toBe(false);
    expect(window.location.search).toBe("?max_age_days=7");

    await userEvent.selectOptions(age, "Older than 30 days");
    await waitFor(() => expect(seen.at(-1)?.get("min_age_days")).toBe("30"));
    expect(window.location.search).toBe("?min_age_days=30");

    await userEvent.selectOptions(age, "Custom range…");
    const from = screen.getByRole("spinbutton", { name: "From" });
    const to = screen.getByRole("spinbutton", { name: "to" });
    await userEvent.clear(from);
    await userEvent.type(from, "10");
    await userEvent.type(to, "5");
    expect(screen.getByRole("alert")).toHaveTextContent("no more than");
    expect(to).toHaveAttribute("aria-invalid", "true");
    await userEvent.clear(to);
    await userEvent.type(to, "20");
    await waitFor(() => {
      expect(seen.at(-1)?.get("min_age_days")).toBe("10");
      expect(seen.at(-1)?.get("max_age_days")).toBe("20");
    });
    expect(window.location.search).toBe("?min_age_days=10&max_age_days=20");

    await userEvent.selectOptions(age, "Any");
    await waitFor(() => expect(seen.at(-1)?.has("min_age_days")).toBe(false));
    expect(window.location.search).toBe("");
  });

  it("starts from the age in the URL", async () => {
    const seen: URLSearchParams[] = [];
    server.use(
      http.get(`${API}/roles`, ({ request }) => {
        seen.push(new URL(request.url).searchParams);
        return HttpResponse.json({ items: page1 });
      }),
    );
    renderWithApi(<RolesPage initialAge={{ min: "", max: 14 }} />);
    await screen.findByRole("table", { name: "Roles" });
    expect(screen.getByRole("combobox", { name: "Age" })).toHaveValue("14");
    expect(seen[0]?.get("max_age_days")).toBe("14");
    expect(seen).toHaveLength(1);
  });

  it("shows an empty state with an import action", async () => {
    server.use(http.get(`${API}/roles`, () => HttpResponse.json({ items: [] })));
    renderWithApi(<RolesPage />);
    expect(await screen.findByRole("heading", { name: "No roles yet" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Import a role" })[0]).toHaveAttribute(
      "href",
      "/roles/new",
    );
  });

  it("shows an error state with the correlation id and retries", async () => {
    let calls = 0;
    server.use(
      http.get(`${API}/roles`, () => {
        calls++;
        return calls === 1
          ? HttpResponse.json(problem(500, "internal"), { status: 500 })
          : HttpResponse.json({ items: page1 });
      }),
    );
    renderWithApi(<RolesPage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("corr-test-0001");
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("table", { name: "Roles" })).toBeInTheDocument();
  });
});

describe("NewRolePage", () => {
  it("imports a role and opens it", async () => {
    let body: unknown;
    server.use(
      http.post(`${API}/roles`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(makeRole({ title: "Platform Engineer" }), { status: 201 });
      }),
    );
    renderWithApi(<NewRolePage />);
    await userEvent.type(
      screen.getByRole("textbox", { name: "Title (required)" }),
      "Platform Engineer",
    );
    await userEvent.type(screen.getByRole("textbox", { name: "Company" }), "Acme Pty Ltd");
    await userEvent.type(screen.getByRole("textbox", { name: "Ad text" }), "Pasted ad");
    await userEvent.click(screen.getByRole("button", { name: "Import role" }));

    await waitFor(() => expect(router.push).toHaveBeenCalledWith(`/roles/${makeRole().id}`));
    expect(body).toEqual({
      source: "manual",
      title: "Platform Engineer",
      company: "Acme Pty Ltd",
      description: "Pasted ad",
      score_now: false,
    });
  });

  it("shows field errors from a validation problem", async () => {
    server.use(
      http.post(`${API}/roles`, () =>
        HttpResponse.json(
          problem(422, "validation-failed", {
            errors: [{ pointer: "/url", message: "Not a valid URL" }],
          }),
          { status: 422 },
        ),
      ),
    );
    renderWithApi(<NewRolePage />);
    await userEvent.type(screen.getByRole("textbox", { name: "Title (required)" }), "X");
    await userEvent.click(screen.getByRole("button", { name: "Import role" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Not a valid URL");
    expect(router.push).not.toHaveBeenCalled();
  });
});
