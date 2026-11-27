import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { Schemas } from "@/lib/api";
import { API, gatedStream, problem, renderWithApi, SSE_HEADERS, sseFrame } from "@/lib/testing";
import { TargetsPage } from "./TargetsPage";

vi.mock("next/navigation", () => ({
  usePathname: () => "/employers",
  useSearchParams: () => new URLSearchParams(window.location.search),
}));

type Target = Schemas["Target"];

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => window.history.replaceState(null, "", "/employers"));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const ACME = "00000000-0000-4000-8000-0000000000d1";
const GLOBEX = "00000000-0000-4000-8000-0000000000d2";
const RUN = "00000000-0000-4000-8000-0000000000f1";

function target(overrides: Partial<Target> & { id: string; name: string }): Target {
  return {
    lane: "A",
    status: "suggested",
    origin: "manual",
    version: 1,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
    ...overrides,
  };
}

/** An in-memory /targets list that the handlers below read and write. */
function serveList(initial: Target[]) {
  const state = { items: [...initial], queries: [] as URLSearchParams[] };
  server.use(
    http.get(`${API}/targets`, ({ request }) => {
      const query = new URL(request.url).searchParams;
      state.queries.push(query);
      const status = query.get("status");
      const lane = query.get("lane");
      const q = query.get("q")?.toLowerCase();
      return HttpResponse.json({
        items: state.items.filter(
          (t) =>
            (!status || t.status === status) &&
            (!lane || t.lane === lane) &&
            (!q || [t.name, t.why].some((v) => (v ?? "").toLowerCase().includes(q))),
        ),
      });
    }),
  );
  return state;
}

function conflict(currentVersion?: number) {
  return HttpResponse.json(
    problem(409, "version-conflict", currentVersion ? { current_version: currentVersion } : {}),
    { status: 409 },
  );
}

describe("TargetsPage", () => {
  it("lists employers with readable labels", async () => {
    serveList([
      target({ id: ACME, name: "Acme Pty Ltd", why: "Strong data team", origin: "ai" }),
      target({ id: GLOBEX, name: "Globex", lane: "B", status: "approved" }),
    ]);
    renderWithApi(<TargetsPage />);
    const table = await screen.findByRole("table", { name: "Employers" });
    const acme = within(table).getByRole("row", { name: /Acme Pty Ltd/ });
    expect(acme).toHaveTextContent("Data platform & architecture");
    expect(acme).toHaveTextContent("Suggested");
    expect(acme).toHaveTextContent("AI suggested");
    expect(acme).toHaveTextContent("Strong data team");
    expect(within(table).getByRole("row", { name: /Globex/ })).toHaveTextContent("Approved");
  });

  it("shows an empty state when there are no employers", async () => {
    serveList([]);
    renderWithApi(<TargetsPage />);
    expect(await screen.findByRole("region", { name: "No employers yet" })).toBeVisible();
  });

  it("shows a failed list with its reference and retries", async () => {
    let calls = 0;
    server.use(
      http.get(`${API}/targets`, () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json(problem(500, "internal"), { status: 500 })
          : HttpResponse.json({ items: [target({ id: ACME, name: "Acme Pty Ltd" })] });
      }),
    );
    renderWithApi(<TargetsPage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Employers failed to load");
    expect(alert).toHaveTextContent("corr-test-0001");
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("row", { name: /Acme Pty Ltd/ })).toBeInTheDocument();
  });

  it("filters by status and lane through the API", async () => {
    const state = serveList([
      target({ id: ACME, name: "Acme Pty Ltd" }),
      target({ id: GLOBEX, name: "Globex", lane: "B", status: "approved" }),
    ]);
    renderWithApi(<TargetsPage />);
    await screen.findByRole("row", { name: /Globex/ });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Status" }), "approved");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Lane" }), "B");
    await waitFor(() =>
      expect(screen.queryByRole("row", { name: /Acme Pty Ltd/ })).not.toBeInTheDocument(),
    );
    const last = state.queries.at(-1);
    expect(last?.get("status")).toBe("approved");
    expect(last?.get("lane")).toBe("B");
  });

  it("creates a target with only the filled-in fields and refreshes the list", async () => {
    const state = serveList([]);
    let body: unknown;
    server.use(
      http.post(`${API}/targets`, async ({ request }) => {
        body = await request.json();
        const created = target({ id: GLOBEX, name: "Initech", lane: "B" });
        state.items.push(created);
        return HttpResponse.json(created, { status: 201 });
      }),
    );
    renderWithApi(<TargetsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Add employer" }));
    const form = screen.getByRole("form", { name: "New employer" });
    await userEvent.type(within(form).getByRole("textbox", { name: /Name/ }), "Initech");
    await userEvent.selectOptions(within(form).getByRole("combobox", { name: "Lane" }), "B");
    await userEvent.type(within(form).getByRole("textbox", { name: "Why" }), "Hiring analysts");
    await userEvent.type(
      within(form).getByRole("textbox", { name: /Sources/ }),
      "https://example.test/a\n\n https://example.test/b ",
    );
    await userEvent.click(within(form).getByRole("button", { name: "Save employer" }));

    expect(await screen.findByRole("row", { name: /Initech/ })).toBeInTheDocument();
    expect(body).toEqual({
      name: "Initech",
      lane: "B",
      status: "suggested",
      why: "Hiring analysts",
      sources: ["https://example.test/a", "https://example.test/b"],
    });
    expect(screen.queryByRole("form", { name: "New employer" })).not.toBeInTheDocument();
  });

  it("shows field errors when the API rejects a new target", async () => {
    serveList([]);
    server.use(
      http.post(`${API}/targets`, () =>
        HttpResponse.json(
          problem(422, "validation-failed", {
            title: "Validation failed",
            errors: [{ pointer: "/sources/0", message: "Must be a link" }],
          }),
          { status: 422 },
        ),
      ),
    );
    renderWithApi(<TargetsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Add employer" }));
    const form = screen.getByRole("form", { name: "New employer" });
    await userEvent.type(within(form).getByRole("textbox", { name: /Name/ }), "Initech");
    await userEvent.type(within(form).getByRole("textbox", { name: /Sources/ }), "not a link");
    await userEvent.click(within(form).getByRole("button", { name: "Save employer" }));
    const alert = await within(form).findByRole("alert");
    expect(alert).toHaveTextContent("Validation failed");
    expect(alert).toHaveTextContent("Must be a link");
  });

  it("on a 409 shows what changed elsewhere and re-saves on the latest version", async () => {
    const state = serveList([target({ id: ACME, name: "Acme Pty Ltd", why: "Old reason" })]);
    const patches: unknown[] = [];
    server.use(
      http.patch(`${API}/targets/:id`, async ({ request }) => {
        const patch = (await request.json()) as Schemas["TargetUpdate"];
        patches.push(patch);
        if (patch.version === 1) return conflict(2);
        const saved = target({
          id: ACME,
          name: "Acme Pty Ltd",
          why: "Changed by someone else",
          status: "approved",
          version: 3,
        });
        state.items = [saved];
        return HttpResponse.json(saved);
      }),
      http.get(`${API}/targets/:id`, () =>
        HttpResponse.json(
          target({ id: ACME, name: "Acme Pty Ltd", why: "Changed by someone else", version: 2 }),
        ),
      ),
    );
    renderWithApi(<TargetsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit Acme Pty Ltd" }));
    const form = screen.getByRole("form", { name: "Edit Acme Pty Ltd" });
    await userEvent.selectOptions(
      within(form).getByRole("combobox", { name: "Status" }),
      "approved",
    );
    await userEvent.click(within(form).getByRole("button", { name: "Save employer" }));

    const alert = await within(form).findByRole("alert");
    expect(alert).toHaveTextContent("This employer changed since you opened it");
    expect(within(alert).getByRole("list", { name: "Changes saved elsewhere" })).toHaveTextContent(
      "Why: Changed by someone else",
    );
    // The user's draft survives the conflict.
    expect(within(form).getByRole("combobox", { name: "Status" })).toHaveValue("approved");

    await userEvent.click(within(alert).getByRole("button", { name: "Save my changes again" }));
    await waitFor(() =>
      expect(screen.queryByRole("form", { name: "Edit Acme Pty Ltd" })).not.toBeInTheDocument(),
    );
    expect(patches).toEqual([
      { version: 1, status: "approved" },
      { version: 2, status: "approved" },
    ]);
    expect(await screen.findByRole("row", { name: /Acme Pty Ltd/ })).toHaveTextContent("Approved");
  });

  it("lets the user discard their edit after a 409", async () => {
    serveList([target({ id: ACME, name: "Acme Pty Ltd" })]);
    server.use(
      http.patch(`${API}/targets/:id`, () => conflict()),
      http.get(`${API}/targets/:id`, () =>
        HttpResponse.json(target({ id: ACME, name: "Acme Pty Ltd", version: 2 })),
      ),
    );
    renderWithApi(<TargetsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit Acme Pty Ltd" }));
    const form = screen.getByRole("form", { name: "Edit Acme Pty Ltd" });
    await userEvent.type(within(form).getByRole("textbox", { name: "Notes" }), "Call Monday");
    await userEvent.click(within(form).getByRole("button", { name: "Save employer" }));
    await userEvent.click(await within(form).findByRole("button", { name: "Discard my changes" }));
    expect(screen.queryByRole("form", { name: "Edit Acme Pty Ltd" })).not.toBeInTheDocument();
  });

  it("deletes after confirmation, sending the version read", async () => {
    const state = serveList([target({ id: ACME, name: "Acme Pty Ltd", version: 4 })]);
    let deleted: string | null = null;
    server.use(
      http.delete(`${API}/targets/:id`, ({ request }) => {
        deleted = new URL(request.url).searchParams.get("version");
        state.items = [];
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithApi(<TargetsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Delete Acme Pty Ltd" }));
    const confirm = screen.getByRole("alertdialog", { name: "Delete Acme Pty Ltd?" });
    expect(within(confirm).getByRole("button", { name: "Yes, delete" })).toHaveFocus();
    await userEvent.click(within(confirm).getByRole("button", { name: "Yes, delete" }));
    expect(await screen.findByRole("region", { name: "No employers yet" })).toBeVisible();
    expect(deleted).toBe("4");
  });

  it("explains a delete that hit a 409 and refreshes the list", async () => {
    const state = serveList([target({ id: ACME, name: "Acme Pty Ltd" })]);
    server.use(
      http.delete(`${API}/targets/:id`, () => {
        state.items = [target({ id: ACME, name: "Acme Pty Ltd", why: "Updated", version: 2 })];
        return conflict(2);
      }),
    );
    renderWithApi(<TargetsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Delete Acme Pty Ltd" }));
    await userEvent.click(screen.getByRole("button", { name: "Yes, delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Acme Pty Ltd changed elsewhere. The list is refreshed; delete again if you still want to.",
    );
    expect(await screen.findByRole("row", { name: /Acme Pty Ltd/ })).toHaveTextContent("Updated");
  });

  it("starts research on a target and streams its steps", async () => {
    serveList([target({ id: ACME, name: "Acme Pty Ltd" })]);
    let release = () => {};
    const gate = new Promise<void>((r) => {
      release = r;
    });
    let startedOn: string | undefined;
    let body: unknown;
    const step = (seq: number, summary: string) => ({
      id: `00000000-0000-4000-8000-0000000002${String(seq).padStart(2, "0")}`,
      run_id: RUN,
      seq,
      kind: "tool_call",
      tool: "web_search",
      summary,
      at: "2026-11-25T01:00:00Z",
    });
    server.use(
      http.post(`${API}/targets/:id/research-runs`, async ({ params, request }) => {
        startedOn = String(params.id);
        body = await request.json();
        return HttpResponse.json(
          { id: RUN, target_id: startedOn, status: "queued", created_at: "2026-11-25T01:00:00Z" },
          { status: 202 },
        );
      }),
      http.get(
        `${API}/research-runs/:runId/steps`,
        () =>
          new HttpResponse(
            gatedStream([sseFrame("step", "0", step(0, "Searching recent news"))], gate, [
              sseFrame("done", "d", { status: "succeeded" }),
            ]),
            { headers: SSE_HEADERS },
          ),
      ),
      http.get(`${API}/research-runs/:runId`, () =>
        HttpResponse.json({
          id: RUN,
          target_id: ACME,
          status: "succeeded",
          summary: "Acme is **hiring**.",
          sources: ["https://example.test/news"],
          created_at: "2026-11-25T01:00:00Z",
        }),
      ),
    );
    renderWithApi(<TargetsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Research Acme Pty Ltd" }));
    const panel = screen.getByRole("region", { name: "Research: Acme Pty Ltd" });
    await userEvent.click(within(panel).getByRole("checkbox", { name: "News" }));
    await userEvent.click(within(panel).getByRole("button", { name: "Start research" }));

    const steps = await within(panel).findByRole("list", { name: "Research steps" });
    expect(await within(steps).findByText("Searching recent news")).toBeInTheDocument();
    expect(within(panel).getByRole("status")).toHaveTextContent("Research running…");
    await act(async () => release());
    expect(await within(panel).findByText("hiring")).toBeInTheDocument();
    expect(within(panel).getByText("https://example.test/news")).toBeInTheDocument();
    expect(startedOn).toBe(ACME);
    expect(body).toEqual({ focus: ["overview", "news"] });

    await userEvent.click(within(panel).getByRole("button", { name: "Close research" }));
    expect(screen.queryByRole("region", { name: "Research: Acme Pty Ltd" })).toBeNull();
  });

  describe("search", () => {
    const field = () => screen.getByRole("searchbox", { name: "Search employers" });
    const two = () => [
      target({ id: ACME, name: "Acme Pty Ltd", why: "Strong data team" }),
      target({ id: GLOBEX, name: "Globex", lane: "B", status: "approved" }),
    ];

    it("sends q once typing pauses, with the filters, and keeps it in the URL", async () => {
      const state = serveList(two());
      renderWithApi(<TargetsPage />);
      await screen.findByRole("row", { name: /Globex/ });
      await userEvent.selectOptions(screen.getByRole("combobox", { name: "Lane" }), "B");
      await userEvent.type(field(), "glo");
      await waitFor(() => expect(state.queries.at(-1)?.get("q")).toBe("glo"));
      expect(state.queries.at(-1)?.get("lane")).toBe("B");
      expect(state.queries.map((q) => q.get("q")).filter(Boolean)).toEqual(["glo"]);
      expect(window.location.search).toBe("?q=glo");
      await waitFor(() =>
        expect(screen.getByRole("status")).toHaveTextContent("1 employer matches “glo”"),
      );
    });

    it("searches every lane when no lane is chosen", async () => {
      const state = serveList(two());
      renderWithApi(<TargetsPage />);
      await screen.findByRole("row", { name: /Globex/ });
      await userEvent.type(field(), "data");
      await waitFor(() => expect(state.queries.at(-1)?.get("q")).toBe("data"));
      expect(state.queries.at(-1)?.has("lane")).toBe(false);
      await waitFor(() =>
        expect(screen.queryByRole("row", { name: /Globex/ })).not.toBeInTheDocument(),
      );
      expect(screen.getByRole("row", { name: /Acme Pty Ltd/ })).toBeInTheDocument();
    });

    it("starts from ?q= in the URL and clears with Escape", async () => {
      window.history.replaceState(null, "", "/employers?q=Acme");
      const state = serveList(two());
      renderWithApi(<TargetsPage />);
      await screen.findByRole("row", { name: /Acme Pty Ltd/ });
      expect(state.queries[0]?.get("q")).toBe("Acme");
      expect(field()).toHaveValue("Acme");
      await userEvent.click(field());
      await userEvent.keyboard("{Escape}");
      expect(field()).toHaveValue("");
      expect(window.location.search).toBe("");
      expect(await screen.findByRole("row", { name: /Globex/ })).toBeInTheDocument();
      expect(state.queries.at(-1)?.has("q")).toBe(false);
    });

    it("says when nothing matches and offers to clear the search", async () => {
      serveList(two());
      renderWithApi(<TargetsPage />);
      await screen.findByRole("row", { name: /Globex/ });
      await userEvent.type(field(), "zzz");
      const empty = await screen.findByRole("region", { name: "No employers match “zzz”" });
      await userEvent.click(within(empty).getByRole("button", { name: "Clear search" }));
      expect(await screen.findByRole("row", { name: /Globex/ })).toBeInTheDocument();
      expect(field()).toHaveValue("");
      expect(field()).toHaveFocus();
    });
  });
});
