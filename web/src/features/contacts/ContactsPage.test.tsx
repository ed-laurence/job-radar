import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { Schemas } from "@/lib/api";
import { API, problem, renderWithApi } from "@/lib/testing";
import { ContactsPage } from "./ContactsPage";

vi.mock("next/navigation", () => ({
  usePathname: () => "/contacts",
  useSearchParams: () => new URLSearchParams(window.location.search),
}));

type Contact = Schemas["Contact"];
const contact = (n: number, extra: Partial<Contact> = {}): Contact => ({
  id: `00000000-0000-4000-8000-0000000000c${n}`,
  first_name: `Alex${n}`,
  last_name: "Example",
  email: `alex${n}@example.test`,
  company: "Acme Pty Ltd",
  status: "new",
  version: 1,
  created_at: "2026-11-01T00:00:00Z",
  updated_at: "2026-11-01T00:00:00Z",
  ...extra,
});

let contacts: Contact[] = [];
let queries: URLSearchParams[] = [];
const matches = (c: Contact, q: string) =>
  [c.first_name, c.last_name, c.company, c.email].some((v) =>
    (v ?? "").toLowerCase().includes(q.toLowerCase()),
  );
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  window.history.replaceState(null, "", "/contacts");
  queries = [];
  contacts = [contact(1), contact(2, { company: "Globex" })];
  server.use(
    http.get(`${API}/contacts`, ({ request }) => {
      const params = new URL(request.url).searchParams;
      queries.push(params);
      const cursor = params.get("cursor");
      if (cursor === "p2") return HttpResponse.json({ items: [contact(3)] });
      const q = params.get("q");
      if (q) return HttpResponse.json({ items: contacts.filter((c) => matches(c, q)) });
      return HttpResponse.json({
        items: contacts,
        ...(contacts.length > 1 ? { next_cursor: "p2" } : {}),
      });
    }),
    http.get(`${API}/contacts/:contactId`, ({ params }) => {
      const found = contacts.find((c) => c.id === params.contactId);
      return found
        ? HttpResponse.json(found)
        : HttpResponse.json(problem(404, "not-found"), { status: 404 });
    }),
  );
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("ContactsPage", () => {
  it("lists contacts in a table and pages with the cursor", async () => {
    renderWithApi(<ContactsPage />);
    const table = await screen.findByRole("table", { name: "Contacts" });
    expect(within(table).getByRole("rowheader", { name: "Alex1 Example" })).toBeInTheDocument();
    expect(within(table).getByText("Globex")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Load more contacts" }));
    expect(
      await within(table).findByRole("rowheader", { name: "Alex3 Example" }),
    ).toBeInTheDocument();
  });

  it("creates a contact", async () => {
    let body: unknown;
    server.use(
      http.post(`${API}/contacts`, async ({ request }) => {
        body = await request.json();
        const created = contact(9, { first_name: "Sam", last_name: "Sample", email: undefined });
        contacts = [...contacts, created];
        return HttpResponse.json(created, { status: 201 });
      }),
    );
    renderWithApi(<ContactsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Add contact" }));
    const form = screen.getByRole("form", { name: "New contact" });
    await userEvent.type(
      within(form).getByRole("textbox", { name: "First name (required)" }),
      "Sam",
    );
    await userEvent.type(within(form).getByRole("textbox", { name: "Last name" }), "Sample");
    await userEvent.selectOptions(
      within(form).getByRole("combobox", { name: "Closeness" }),
      "know",
    );
    await userEvent.click(within(form).getByRole("button", { name: "Save contact" }));

    expect(await screen.findByRole("rowheader", { name: "Sam Sample" })).toBeInTheDocument();
    expect(body).toEqual({
      first_name: "Sam",
      last_name: "Sample",
      status: "new",
      closeness: "know",
    });
    expect(screen.queryByRole("form", { name: "New contact" })).not.toBeInTheDocument();
  });

  it("edits with the version, and on a 409 shows the latest and retries", async () => {
    const bodies: Schemas["ContactUpdate"][] = [];
    server.use(
      http.patch(`${API}/contacts/:contactId`, async ({ request }) => {
        const body = (await request.json()) as Schemas["ContactUpdate"];
        bodies.push(body);
        if (body.version === 1) {
          contacts = [contact(1, { company: "Initech", version: 2 }), contact(2)];
          return HttpResponse.json(problem(409, "version-conflict", { current_version: 2 }), {
            status: 409,
          });
        }
        const updated = contact(1, { company: "Initech", status: "sent", version: 3 });
        contacts = [updated, contact(2)];
        return HttpResponse.json(updated);
      }),
    );
    renderWithApi(<ContactsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit Alex1 Example" }));
    const form = screen.getByRole("form", { name: "Edit Alex1 Example" });
    await userEvent.selectOptions(within(form).getByRole("combobox", { name: "Status" }), "sent");
    await userEvent.click(within(form).getByRole("button", { name: "Save contact" }));

    const alert = await within(form).findByRole("alert");
    expect(alert).toHaveTextContent(/changed since you opened it/);
    expect(await within(alert).findByText(/Initech/)).toBeInTheDocument();
    await userEvent.click(within(alert).getByRole("button", { name: "Save my changes again" }));

    await waitFor(() =>
      expect(screen.queryByRole("form", { name: "Edit Alex1 Example" })).not.toBeInTheDocument(),
    );
    expect(bodies).toEqual([
      { version: 1, status: "sent" },
      { version: 2, status: "sent" },
    ]);
    expect(await screen.findByText("Initech")).toBeInTheDocument();
  });

  it("deletes after confirming", async () => {
    let version: string | null = null;
    server.use(
      http.delete(`${API}/contacts/:contactId`, ({ request }) => {
        version = new URL(request.url).searchParams.get("version");
        contacts = [contact(2)];
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithApi(<ContactsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Delete Alex1 Example" }));
    const dialog = screen.getByRole("alertdialog", { name: "Delete Alex1 Example?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Yes, delete" }));
    await waitFor(() =>
      expect(screen.queryByRole("rowheader", { name: "Alex1 Example" })).not.toBeInTheDocument(),
    );
    expect(version).toBe("1");
  });

  it("shows empty and error states", async () => {
    contacts = [];
    const view = renderWithApi(<ContactsPage />);
    expect(await screen.findByRole("heading", { name: "No contacts yet" })).toBeInTheDocument();
    view.unmount();
    server.use(
      http.get(`${API}/contacts`, () =>
        HttpResponse.json(problem(500, "internal"), { status: 500 }),
      ),
    );
    renderWithApi(<ContactsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("corr-test-0001");
  });

  describe("search", () => {
    const field = () => screen.getByRole("searchbox", { name: "Search contacts" });

    it("sends q after a pause, keeps it in the URL and announces the result", async () => {
      renderWithApi(<ContactsPage />);
      await screen.findByRole("table", { name: "Contacts" });
      await userEvent.type(field(), "glob");
      await waitFor(() => expect(queries.at(-1)?.get("q")).toBe("glob"));
      // Debounced: the keystrokes in between never reached the API.
      expect(queries.map((q) => q.get("q")).filter(Boolean)).toEqual(["glob"]);
      expect(window.location.search).toBe("?q=glob");
      await waitFor(() =>
        expect(screen.queryByRole("rowheader", { name: "Alex1 Example" })).not.toBeInTheDocument(),
      );
      expect(screen.getByRole("rowheader", { name: "Alex2 Example" })).toBeInTheDocument();
      expect(screen.getByRole("status")).toHaveTextContent("1 contact matches “glob”");
    });

    it("waits about 250 ms after the last keystroke before searching", async () => {
      let searchedAt = 0;
      server.use(
        http.get(`${API}/contacts`, ({ request }) => {
          const params = new URL(request.url).searchParams;
          queries.push(params);
          if (params.has("q")) searchedAt = performance.now();
          return HttpResponse.json({ items: contacts });
        }),
      );
      renderWithApi(<ContactsPage />);
      await screen.findByRole("table", { name: "Contacts" });
      await userEvent.type(field(), "x");
      const typedAt = performance.now();
      await waitFor(() => expect(searchedAt).toBeGreaterThan(0));
      expect(searchedAt - typedAt).toBeGreaterThanOrEqual(200);
    });

    it("starts from ?q= in the URL", async () => {
      window.history.replaceState(null, "", "/contacts?q=Globex");
      renderWithApi(<ContactsPage />);
      expect(await screen.findByRole("rowheader", { name: "Alex2 Example" })).toBeInTheDocument();
      expect(field()).toHaveValue("Globex");
      expect(queries[0]?.get("q")).toBe("Globex");
    });

    it("goes back to the first page when the search changes", async () => {
      renderWithApi(<ContactsPage />);
      await userEvent.click(await screen.findByRole("button", { name: "Load more contacts" }));
      await screen.findByRole("rowheader", { name: "Alex3 Example" });
      await userEvent.type(field(), "alex");
      await waitFor(() => expect(queries.at(-1)?.get("q")).toBe("alex"));
      expect(queries.at(-1)?.has("cursor")).toBe(false);
      await waitFor(() =>
        expect(screen.queryByRole("rowheader", { name: "Alex3 Example" })).not.toBeInTheDocument(),
      );
    });

    it("clears with Escape", async () => {
      window.history.replaceState(null, "", "/contacts?q=Globex");
      renderWithApi(<ContactsPage />);
      await screen.findByRole("rowheader", { name: "Alex2 Example" });
      await userEvent.click(field());
      await userEvent.keyboard("{Escape}");
      expect(field()).toHaveValue("");
      expect(window.location.search).toBe("");
      expect(await screen.findByRole("rowheader", { name: "Alex1 Example" })).toBeInTheDocument();
      expect(queries.at(-1)?.has("q")).toBe(false);
    });

    it("says when nothing matches and offers to clear the search", async () => {
      renderWithApi(<ContactsPage />);
      await screen.findByRole("table", { name: "Contacts" });
      await userEvent.type(field(), "zzz");
      const empty = await screen.findByRole("region", { name: "No contacts match “zzz”" });
      expect(screen.getByRole("status")).toHaveTextContent("No contacts match “zzz”");
      await userEvent.click(within(empty).getByRole("button", { name: "Clear search" }));
      expect(await screen.findByRole("rowheader", { name: "Alex1 Example" })).toBeInTheDocument();
      expect(field()).toHaveValue("");
      expect(field()).toHaveFocus();
      expect(window.location.search).toBe("");
    });
  });
});
